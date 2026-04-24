# Деплой бота (Docker)

## Инфраструктура

| Сервер | IP | SSH-ключ | Назначение |
|--------|-----|----------|------------|
| **App Server** | 143.110.233.82 (sf-bot-app01) | `~/.ssh/luk` | Docker-контейнер с ботом |

Бот делит сервер с `example_bot`. Собственной БД не требуется — состояние
уведомлений хранится в docker volume `tg-jira-tasks_bot_data`
(`/app/data/sync_state.json`).

## Требования

- Docker + Docker Compose на App Server
- SSH-доступ к серверу
- `rsync` (для синхронизации кода)

---

## Первичная установка

### 1. Создать каталог на сервере

```bash
ssh -i ~/.ssh/luk root@143.110.233.82 "mkdir -p /root/tg-jira-tasks"
```

### 2. Синхронизировать код

Из корня локального репозитория:

```bash
rsync -avz --delete \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='.claude/' \
  --exclude='.osgrep/' \
  --exclude='.mcp.json' \
  --exclude='.DS_Store' \
  --exclude='CLAUDE.md' \
  --exclude='ANTIGRAVITY.md' \
  --exclude='tasks/' \
  --exclude='.env' \
  --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/luk" \
  ./ root@143.110.233.82:/root/tg-jira-tasks/
```

### 3. Создать `.env` на сервере

```bash
ssh -i ~/.ssh/luk root@143.110.233.82 "cat > /root/tg-jira-tasks/.env" << 'EOF'
TELEGRAM_TOKEN=<токен_бота>
JIRA_URL=https://jira.avosend.tech:443
JIRA_PAT=<personal_access_token>
ALLOWED_USERS=<telegram_user_ids>
EOF
```

### 4. Запуск

```bash
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "cd /root/tg-jira-tasks && docker compose up -d --build"
```

### 5. Проверка

```bash
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "docker ps --filter name=tg-jira-bot && docker logs --tail=30 tg-jira-bot"
```

В логах должно быть `Run polling for bot @<botname>`.

---

## Обновление

```bash
# 1. Синхронизировать код (та же rsync-команда, что при установке)
rsync -avz --delete ... root@143.110.233.82:/root/tg-jira-tasks/

# 2. Пересобрать и перезапустить
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "cd /root/tg-jira-tasks && docker compose up -d --build"

# 3. Проверить логи
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "cd /root/tg-jira-tasks && docker compose logs --tail=30"
```

---

## Полезные команды

| Команда | Описание |
|---------|----------|
| `docker compose logs -f` | Потоковое чтение логов |
| `docker compose logs --tail=50` | Последние 50 строк |
| `docker compose restart` | Перезапуск контейнера |
| `docker compose down` | Остановка |
| `docker compose ps` | Статус контейнера |
| `docker exec -it tg-jira-bot sh` | Войти в контейнер |
| `docker volume inspect tg-jira-tasks_bot_data` | Инфо по volume с состоянием |

Все команды выполняются в каталоге `/root/tg-jira-tasks` на App Server.

---

## Состояние уведомлений

Подписки `/sync` хранятся в `/app/data/sync_state.json` внутри контейнера,
volume — `tg-jira-tasks_bot_data`. Сохраняется между перезапусками.

Бэкап состояния:

```bash
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "docker cp tg-jira-bot:/app/data/sync_state.json /tmp/sync_state.json"
scp -i ~/.ssh/luk root@143.110.233.82:/tmp/sync_state.json ./backups/
```

---

## Диагностика

### Бот не стартует

```bash
# Полные логи
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "cd /root/tg-jira-tasks && docker compose logs --tail=200"

# Проверка .env
ssh -i ~/.ssh/luk root@143.110.233.82 \
  "docker exec tg-jira-bot env | grep -E 'TELEGRAM|JIRA|ALLOWED'"
```

### Конфликт токена Telegram

Если другой инстанс бота уже делает polling с тем же токеном, aiogram
отвечает `TelegramConflictError`. Решение — остановить другой инстанс
или выпустить новый токен у `@BotFather`.
