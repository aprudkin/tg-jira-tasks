# Telegram Jira Tasks Bot

Telegram-бот для получения задач из Jira. Показывает задачи текущего пользователя в статусе "In Progress".

## Требования

- Docker
- Telegram Bot Token (от [@BotFather](https://t.me/BotFather))
- Jira API Token

## Установка

## Настройка

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
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
JIRA_EMAIL=your_email@company.com
JIRA_API_TOKEN=your_jira_api_token
ALLOWED_USERS=123456789,987654321
```

### Получение токенов

**Telegram Bot Token:**
1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot` и следуйте инструкциям
3. Скопируйте полученный токен

**Jira API Token:**
1. Перейдите в [Atlassian Account Settings](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Нажмите "Create API token"
3. Скопируйте токен

**Telegram User ID:**
- Отправьте сообщение боту [@userinfobot](https://t.me/userinfobot) для получения вашего ID

### Docker

```bash
docker build -t tg-jira-bot .
docker run --env-file .env tg-jira-bot
```

### Docker Compose

```bash
docker-compose up --build
```

Для запуска в фоновом режиме:
```bash
docker-compose up -d --build
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и список команд |
| `/tasks` | Показать задачи в статусе "In Progress" |

## Переменные окружения

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `TELEGRAM_TOKEN` | Токен Telegram бота | Да |
| `JIRA_URL` | URL Jira сервера | Да |
| `JIRA_EMAIL` | Email аккаунта Jira | Да |
| `JIRA_API_TOKEN` | API токен Jira | Да |
| `ALLOWED_USERS` | ID пользователей через запятую (пусто = все) | Нет |


## Архитектура

Telegram-бот (aiogram 3.x), который интегрируется с Jira для получения задач пользователя.

**Ключевые компоненты:**
- `bot/main.py` - Точка входа, настраивает Dispatcher с middleware и роутерами
- `bot/config.py` - Настройки через pydantic-settings, загружаются из переменных окружения
- `bot/services/jira.py` - Класс JiraService, обертка над библиотекой jira-python, использует JQL запросы
- `bot/handlers/tasks.py` - Обработчики команд (Router pattern из aiogram)
- `bot/middlewares/auth.py` - Middleware для фильтрации пользователей Telegram (белый список)

**Поток:** Команда пользователя → AuthMiddleware (проверка белого списка) → Handler → JiraService → Ответ

## Разработка

Правила разработки и архитектурные детали описаны в файле [ANTIGRAVITY.md](ANTIGRAVITY.md).

## Лицензия

MIT
