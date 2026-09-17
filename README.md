# Telegram Jira Tasks Bot

Telegram-бот на Python 3.11 и aiogram 3 для просмотра задач Jira и доставки изменений в Telegram. Поддерживает Jira Cloud и Jira Data Center, личный канал и независимые каналы отслеживаемых коллег.

## Быстрый запуск

Требуются Docker с Compose, Telegram bot token и один из поддерживаемых вариантов авторизации Jira.

```bash
git clone git@github.com:aprudkin/tg-jira-tasks.git
cd tg-jira-tasks
cp .env.example .env
# Заполните .env, затем:
docker compose up --build
```

Compose запускает бота и хранит состояние уведомлений в named volume `bot_data`. Для фонового запуска добавьте `-d`. Telegram-токен создаётся через [@BotFather](https://t.me/BotFather).

## Конфигурация

`pydantic-settings` читает переменные окружения и локальный `.env`. Значения секретов не следует коммитить или печатать в диагностике.

| Переменная | Назначение | Требование |
|---|---|---|
| `TELEGRAM_TOKEN` | Telegram bot token | Всегда |
| `JIRA_URL` | Базовый URL Jira без пути задачи | Всегда |
| `JIRA_EMAIL` | Email Jira Cloud для basic auth | Вместе с `JIRA_API_TOKEN`, если нет PAT |
| `JIRA_API_TOKEN` | API token Jira Cloud | Вместе с `JIRA_EMAIL`, если нет PAT |
| `JIRA_PAT` | Personal Access Token Jira Data Center | Альтернатива Cloud-паре; имеет приоритет |
| `ALLOWED_USERS` | Положительные Telegram user ID через запятую | Непустой, если `ALLOW_OPEN_ACCESS=false` |
| `ALLOWED_CHAT_IDS` | Разрешённые отрицательные ID group/supergroup через запятую | Необязательно; без него группы закрыты |
| `ALLOW_OPEN_ACCESS` | Разрешить любого известного отправителя в private chat | Необязательно, по умолчанию `false`; группы не открывает |
| `STATE_FILE` | Путь к JSON-состоянию уведомлений | Необязательно, по умолчанию `/app/data/sync_state.json` |

Для локального запуска вне контейнера задайте доступный путь, например `STATE_FILE=./data/sync_state.json`. Бот требует либо `JIRA_PAT`, либо одновременно `JIRA_EMAIL` и `JIRA_API_TOKEN`; если заданы оба способа, используется PAT.

Команды в private chat разрешены только отправителям из `ALLOWED_USERS`, кроме явно включённого открытого режима. В group/supergroup должны быть разрешены и отправитель, и chat ID. Сообщения без известного отправителя и неподдерживаемые типы чатов отклоняются. Та же политика повторно проверяется перед восстановлением фоновой доставки.

При обновлении старой установки с пустым `ALLOWED_USERS` заполните whitelist или явно включите `ALLOW_OPEN_ACCESS=true`: иначе запуск будет отклонён. Для существующего subscribed chat разрешите его владельца (private) или chat ID (group). Если чат исключён из политики, его каналы и cursor сохраняются, но доставка блокируется до исправления настройки и рестарта.

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Справка |
| `/inprog` | Мои задачи в `In Progress` |
| `/todo` | Мой backlog (`To Do`, `Backlog`, `Open`) |
| `/waiting` | Мои задачи в `Discussion` или `On Hold` |
| `/sprint` | Мои задачи активного спринта, сгруппированные по статусу |
| `/recent` | Мои задачи, обновлённые за 24 часа, сгруппированные по статусу |
| `/watching` | Незавершённые задачи, где я watcher, но не assignee |
| `/byme` | Незавершённые задачи, созданные мной и назначенные другим |
| `/stats` | Счётчики задач |
| `/sync [мин]` | Создать личный канал или изменить его интервал |
| `/unsync` | Удалить только личный канал |
| `/track <user> [эмодзи] [мин]` | Создать или обновить канал коллеги |
| `/untrack <user>` | Удалить канал коллеги |
| `/tracks` | Показать каналы коллег и, если он включён, личный канал |
| `/silent [user]` | Доставлять события автора без звука; без аргумента — текущий Jira user |
| `/unsilent [user]` | Вернуть звук для событий автора |

Интервалы `/sync` и `/track` — целые числа от 1 до 1440 минут, по умолчанию 30. В `/track` необязательные эмодзи и интервал можно указывать в любом порядке после Jira username.

## Контракт уведомлений

Бот обслуживает один **subscribed chat**. В нём может быть несколько независимых **sync channels**:

- личный канал (`/sync`) выбирает задачи, где `currentUser()` — assignee, reporter или watcher;
- каждый канал коллеги (`/track`) выбирает только задачи, назначенные указанному Jira user;
- у каждого канала собственные интервал, UTC cursor, marker emoji и история deduplication;
- muted authors общие для всех каналов.

`/unsync` не удаляет каналы коллег. Привязка чата освобождается только после удаления последнего канала. `/track` сначала выполняет ограниченную Jira-пробу: нулевой результат допустим, но ошибка API не создаёт канал и не привязывает чат. Другой чат не может перехватить существующую привязку.

Опрос фиксирует верхнюю границу до Jira-запроса и повторяет перекрывающееся UTC-окно `(cursor - 10 минут, верхняя граница]`. Cursor продвигается только после полного Jira-чтения, доставки всех событий и durable state write. Доставленные event ID подтверждаются отдельно для каждого канала, поэтому одно событие общей задачи намеренно может прийти по двум каналам. Сбой оставляет cursor на прежнем месте; после crash между Telegram-ответом и записью state возможен повтор. Гарантия доставки — **at-least-once**, а не exactly-once.

Состояние записывается через временный файл и atomic replace. Изменения каналов, subscribed chat и muted authors публикуются в памяти только после успешной записи; удаление сначала останавливает и дренирует канал, а при ошибке записи восстанавливает его. Весь state валидируется до восстановления. Повреждённый JSON, неизвестная schema или неверное поле блокируют polling и последующие записи, не перезаписывая исходный файл. Поддерживаемые старые схемы мигрируют автоматически с новой UTC baseline, без исторического flood.

Подробные решения: [independent channels](docs/adr/0001-independent-sync-channels.md), [per-channel deduplication](docs/adr/0002-per-channel-dedup.md) и [UTC event window](docs/adr/0003-utc-event-window.md).

## Jira Cloud и Data Center

- Cloud использует `enhanced_search_issues` с page token; Data Center — обычный offset search. Jira-клиент создаётся лениво и сетевые вызовы выполняются вне event loop.
- Поиски читают все страницы в ограниченных пределах и проверяют прогресс пагинации. Интерактивный список ограничен 500 задачами, event poll — 2000 задачами; превышение лимита, malformed metadata или незавершённая страница дают ошибку вместо частичного результата.
- `/stats` получает Data Center `total` минимальным запросом. Cloud считает уникальные задачи по token pages, максимум 10 000 задач и 100 страниц; при недоказанной полноте счётчик не показывается.
- Для усечённых comments Cloud и Data Center используют поддерживаемую offset pagination. Для усечённого changelog Cloud запрашивает продолжение, а Data Center poll завершается fail-closed, потому что поддерживаемого продолжения в этой реализации нет.
- На одну задачу читается не более 5000 записей history, на poll — не более 20 000. Неполный search/history не продвигает cursor.
- События: создание задачи, комментарий на незакрытой задаче, смена статуса и назначение на tracked user. Jira-событие, которое стало видимо в search позже десятиминутного overlap, может быть пропущено.

Точные границы и восстановление описаны в [ARCHITECTURE.md](ARCHITECTURE.md) и [ADR-0003](docs/adr/0003-utc-event-window.md).

## Разработка и проверки

Зависимости и Python `>=3.11,<3.12` зафиксированы в `pyproject.toml` и `uv.lock`; требуется uv `>=0.12.12,<0.13`.

```bash
uv sync --locked --group dev
task test
task security
task docker:build
```

`task test` запускает полный pytest suite с fake Jira/Telegram и временным state, без production-секретов. `task security` запускает Bandit и audit экспортированных runtime-зависимостей. `task docker:build` проверяет secret-free image build. `task init` предназначен не для подготовки тестов: он собирает и запускает реальный bot stack.

GitHub Actions на каждый push и pull request повторяет locked install, полный test suite, security checks и Docker build, а также проверяет непривилегированного runtime user. Production deployment работает только с закоммиченным `HEAD`; безопасный порядок, readiness и rollback описаны в [DEPLOY.md](DEPLOY.md).

### Контролируемое обновление зависимостей

Dependabot еженедельно предлагает обновления Python-зависимостей, GitHub Actions и Docker images. Вручную обновляйте один пакет через `uv lock --upgrade-package <package>`, весь набор — через `uv lock --upgrade`; не редактируйте lock вручную. Проверяйте diff и повторяйте команды установки, тестов, security-проверки и сборки выше. Для Docker сохраняйте точный version tag и проверенный registry digest; Actions закреплены commit SHA. Поддерживаемая версия runtime — Python 3.11.

Обзор компонентов и runtime-контрактов: [ARCHITECTURE.md](ARCHITECTURE.md). Термины предметной области: [CONTEXT.md](CONTEXT.md). Инструкции для агентов: [AGENTS.md](AGENTS.md).

## Лицензия

В репозитории нет файла `LICENSE`; намерение владельца относительно лицензии не подтверждено. Условия использования и распространения требуют уточнения у владельца. Прежнее упоминание MIT не заменяет лицензионный текст.
