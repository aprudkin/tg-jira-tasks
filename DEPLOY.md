# Деплой бота на nc-bot-01

## Текущая установка

Миграция выполнена 06.11.2026. Владелец проверил `/start` у `@quiet_whisper_bot`.
Старые контейнер, state volume и deployment directory удалены.

| Объект | Значение |
| --- | --- |
| SSH | `nc-bot-01`, пользователь `ops`, ключ `~/.ssh/luk`, strict host checking |
| Управление | Явное `sudo -n`; root SSH не используется |
| Каталог | `/srv/tg-jira-bot` |
| Контейнер / Compose project | `tg-jira-bot` / `tg_jira` |
| Production Compose | `docker-compose.nc.yml`; локальный `docker-compose.yml` остаётся dev-конфигурацией |
| Секреты | `/srv/tg-jira-bot/runtime.env`, root:root, 0600; значения не хранятся в Git |
| State | External volume `tg_jira_bot_data`, `/app/data/sync_state.json` |
| Сеть | External network `tg-jira`, `br-tgjira`, `172.29.42.0/24`, без опубликованных портов |
| Runtime | UID/GID 10001:10001, read-only root, cap-drop ALL, no-new-privileges |
| Лимиты | 256 MiB, 0.5 CPU, 128 PID; writable state volume и `/tmp` tmpfs |
| Логи | Docker `local`, 10 MiB × 3 файла |

На том же хосте работают `example_bot` и его PostgreSQL. Workflow управляет
только сервисом `bot`: не выполняет общий `compose down` и не меняет другие
контейнеры/volumes. Для Jira отдельная сеть с DNS/HTTPS egress; состояние
уведомлений находится вне синхронизируемого каталога.

## Выполненный перенос

Перенесён точный рабочий образ ревизии
`dec0f9bdebb24cb078a2d81d02403d9dcdb48a6c`, image ID
`sha256:f2712a6174bcef2bc3395d0ae782d10f41477f3a26052fc7d7210988264e661b`.
Текущий локальный tag — `tg-jira-bot:dec0f9bdebb2`.

Старый экземпляр штатно остановлен перед финальным копированием state. Файл
совпал побайтно, schema 2 прошла serialize/load roundtrip кодом того же образа.
Сохранены subscribed chat, канал, interval, marker, cursor и dedup state.
После старта cursor не откатился назад, polling работает, restart count 0.
Telegram getMe/getWebhookInfo и Jira myself доступны с нового сервера; владелец
подтвердил обычное меню. Между остановкой старого и стартом нового контейнера
прошло около 25 секунд.

Приватные материалы переноса: `/root/nc-followup-20261106T050032Z/` на новом
сервере. Внешний backup и внешнее хранилище исключены из этого объёма работ.
`/srv/tg-jira-bot/source` — исторический source snapshot старой установки;
находящиеся в нём прежние команды с адресом старого сервера не используются.

## Обновление существующей установки

```bash
uv run --locked --group dev python -m pytest tests/ -v
# После коммита:
task deploy:dry
task deploy
```

`task deploy` архивирует только committed HEAD. Allowlist в `scripts/deploy.py`:
Dockerfile, оба Compose-файла, `.dockerignore`, `pyproject.toml`, `uv.lock`, `bot/`,
deployment helpers и `.deploy-revision`. Рабочие `.env*`, `runtime.env`, backups,
state и agent runtime data в payload не попадают. Receiver-side filters допускают
`rsync --delete` только внутри managed `bot/` и `scripts/`; остальные корневые
пути защищены. Raw rsync transport использует SSH alias и `sudo -n rsync`.

Удалённые scripts выполняются через `sshai` на `nc-bot-01` с явным sudo wrapper.
Preflight требует существующие production Compose, runtime.env и ожидаемый volume;
bootstrap нового хоста не выполняется. Он не печатает интерполированную конфигурацию.

Сборка выполняется отдельным Docker-container BuildKit builder через сеть
`example-bot-build`: default bridge на хосте отключён. Builder получает 2 GiB /
2 CPU и удаляется после сборки. Runtime dependencies устанавливаются по lockfile.

До остановки bot новый image проверяет конфигурацию без вывода значений.
После остановки создаётся приватная локальная копия state в
`/srv/tg-jira-bot/backups/<revision>-<UTC timestamp>/`; проверка совместимости
выполняется на временной копии. One-off state helpers используют network none.
Затем проверяются runtime UID, writable state path, ровно один контейнер и
`Run polling for bot` в логах только после его текущего StartedAt. Бюджет — 90 секунд.

## Откат обновления

Сбой после остановки удаляет неуспешный контейнер. После успешного snapshot
восстанавливается pre-deploy state, затем предыдущий image; если snapshot не
завершился, нетронутый state сохраняется. Ошибка restore оставляет bot остановленным,
не запускает старый image на неопределённом состоянии. Readiness проверяется снова.

Восстановление старого cursor может повторно доставить событие: гарантия остаётся
at-least-once согласно ADR-0003. Изменение state schema требует отдельной проверки
обратной совместимости. Это локальный rollback обновления, не внешний backup.

## Диагностика

```bash
ssh nc-bot-01 'sudo -n docker inspect --format "{{.State.Status}} {{.RestartCount}}" tg-jira-bot'
ssh nc-bot-01 'sudo -n docker compose --project-directory /srv/tg-jira-bot -f /srv/tg-jira-bot/docker-compose.nc.yml ps'
```

Не печатайте `docker exec ... env`, `docker inspect` целиком, state JSON или
`docker compose config` без `--quiet`: это раскрывает credentials и пользовательские
данные. Startup helper сообщает статус, не публикуя raw application logs.
