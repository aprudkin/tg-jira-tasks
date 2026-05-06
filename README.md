# Telegram Jira Tasks Bot

Telegram-бот для работы с Jira. Показывает задачи, статистику и отправляет уведомления об изменениях.

## Требования

- Docker
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

## Переменные окружения

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `TELEGRAM_TOKEN` | Токен Telegram бота | Да |
| `JIRA_URL` | URL Jira сервера | Да |
| `JIRA_EMAIL` | Email аккаунта Jira (для Cloud) | Да (если нет PAT) |
| `JIRA_API_TOKEN` | API токен Jira (для Cloud) | Да (если нет PAT) |
| `JIRA_PAT` | Personal Access Token (для DC/Server) | Да (если нет Cloud auth) |
| `ALLOWED_USERS` | ID пользователей через запятую (пусто = все) | Нет |


## Архитектура

Telegram-бот (aiogram 3.x), который интегрируется с Jira для получения задач пользователя.

**Ключевые компоненты:**
- `bot/main.py` - Точка входа, настраивает Dispatcher с middleware и роутерами
- `bot/config.py` - Настройки через pydantic-settings, загружаются из переменных окружения
- `bot/services/jira.py` - Класс JiraService, обертка над библиотекой jira-python, использует JQL запросы
- `bot/services/notifications.py` - Сервис фоновых уведомлений с персистентным состоянием
- `bot/handlers/tasks.py` - Обработчики команд (Router pattern из aiogram)
- `bot/middlewares/auth.py` - Middleware для фильтрации пользователей Telegram (белый список)

**Поток:** Команда пользователя → AuthMiddleware (проверка белого списка) → Handler → JiraService → Ответ

**Уведомления:** Состояние `/sync` сохраняется в `/app/data/sync_state.json` и восстанавливается после перезапуска.

Подробнее см. [ARCHITECTURE.md](ARCHITECTURE.md).

## Разработка

Правила разработки и архитектурные детали описаны в файле [ANTIGRAVITY.md](ANTIGRAVITY.md).

## Лицензия

MIT
