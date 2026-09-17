# Деплой бота (Docker)

## Инфраструктура и границы

| Сервер | SSH alias | Каталог | Контейнер | State volume |
|---|---|---|---|---|
| sf-bot-app01 (`143.110.233.82`) | `sf-bot-app01` | `/root/tg-jira-tasks` | `tg-jira-bot` | `tg-jira-tasks_bot_data` |

Сервер также обслуживает `example_bot`. Деплой управляет только сервисом `bot`,
не выполняет `docker compose down` и не затрагивает другие Compose-проекты.
Состояние уведомлений находится в named volume по пути
`/app/data/sync_state.json`, а не в синхронизируемом каталоге.

Для команд на сервере workflow использует `sshai` с настроенным alias
`sf-bot-app01`. Только `rsync` использует документированный raw SSH transport с
ключом `~/.ssh/luk` и существующей политикой host key. Не меняй SSH-настройки в
рамках обычного деплоя.

## Что переносится

`task deploy` всегда архивирует **закоммиченный `HEAD`**, а не рабочее дерево.
Allowlist находится в `scripts/deploy.py` и ограничен:

- `.dockerignore`, `Dockerfile`, `docker-compose.yml`;
- `pyproject.toml`, `uv.lock`;
- `bot/`;
- deployment helpers из `scripts/`;
- сгенерированным `.deploy-revision` с полным commit SHA.

Поэтому untracked-файлы, `.env*`, `.pi/`, `.memory/`, тестовые артефакты и
локальные credentials не могут попасть в payload. `.dockerignore` также
запрещает build context по умолчанию и разрешает только файлы приложения,
нужные Dockerfile.

Receiver-side фильтры ограничивают `rsync --delete`: устаревшие файлы удаляются
только внутри управляемых `bot/` и `scripts/`. Все остальные корневые пути на
сервере защищены, включая `.env*`, `.pi/`, `.memory/`, `backups/`, `secrets/`,
`data/`, документацию и файлы других workflows.

## Только обновление существующей установки

`task deploy` и `task deploy:dry` поддерживают только уже установленный bot.
Preflight требует существующие `/root/tg-jira-tasks`, `.env`,
`docker-compose.yml`, валидный Compose project и не более одного bot-контейнера.
Bootstrap нового хоста этим workflow намеренно не поддерживается: подготовка
production `.env`, первого Compose project и state volume остаётся отдельной
ручной операцией. Не обходи preflight копированием credentials через `sshai`.

## Обычный деплой

```bash
# Локальные проверки до коммита
uv run --locked --group dev python -m pytest tests/ -v

# После коммита: read-only preflight и точный rsync plan; production не меняется
task deploy:dry

# Тот же committed snapshot: upload, build, restart, readiness и rollback
task deploy
```

`task deploy` выполняет следующий порядок:

1. Создаёт временный allowlisted `git archive HEAD` и проверяет payload.
2. Через `sshai` проверяет существующий Compose project, `.env`, не более одного
   bot-контейнера и mount строго ожидаемого state volume. Значения environment
   и state не выводятся.
3. Показывает `rsync --dry-run`, затем синхронизирует тот же snapshot.
4. Собирает image `tg-jira-tasks-bot:<commit SHA>`.
5. Запускает secret-safe configuration check: pydantic проверяет известные
   форматы, а diagnostic проверяет непустое наличие обязательных настроек и
   допустимой комбинации Jira-auth — без значений, URL, ID и длин. Это не
   проверка токенов или сетевой доступности Jira/Telegram.
6. Останавливает только `bot`, делает приватный backup state и проверяет
   временную копию state новым image. Production state при этой проверке не
   мигрирует.
7. Один раз меняет ownership только внутри `tg-jira-tasks_bot_data` на
   `10001:10001` и доказывает чтение state/запись в каталог от runtime UID.
8. Запускает ровно один контейнер новым image.
9. До 90 секунд ждёт `Run polling for bot` только в логах после текущего
   `StartedAt`; контейнер обязан всё время оставаться `running` без restart.
   Поэтому нормальная начальная задержка Telegram учитывается, а старый marker
   не даёт ложного успеха.

Ненулевой код любого шага означает ошибку deployment. Скрипт не печатает логи
бота автоматически: сетевой exception теоретически может включать чувствительный
URL. Для расследования просматривай ограниченный фрагмент вручную, не публикуя
его в issue.

## Rollback

После остановки bot любой сбой удаляет неуспешный bot-контейнер, чтобы
исключить две polling-реплики. Дальнейшее поведение зависит от snapshot:

- после успешного backup восстанавливается точная pre-deploy копия
  `sync_state.json` (или его отсутствие), затем запускается предыдущий image и
  повторяется readiness check по текущему `StartedAt`;
- если read-only backup не завершился, state ещё не изменялся: restore не
  выполняется, предыдущий image запускается на нетронутом state;
- если restore завершился ошибкой, предыдущий image **не запускается** на
  неопределённом state; bot остаётся остановлен и требуется ручное устранение.

Backup хранится в `/root/tg-jira-tasks/backups/<revision>-<UTC timestamp>/` с
закрытыми правами и защищён от `rsync --delete`. Восстановление старого state
может повторно доставить событие: это ожидаемое следствие гарантии at-least-once
из ADR-0003.

Этот rollback локально проверяется на текущей state schema. Изменение
`STATE_SCHEMA_VERSION` требует отдельной проверки обратной совместимости до
деплоя; нельзя считать image rollback безопасным только из-за наличия backup.
При первом install предыдущего image нет — автоматический image rollback тогда
невозможен, о чём задача сообщает ошибкой.

Для ручного rollback используй backup и предыдущий image по тому же порядку:
сначала остановить/удалить `tg-jira-bot`, затем восстановить state, затем
запустить **один** service `bot` и выполнить
`scripts/wait_for_startup.py tg-jira-bot --timeout 90`. Не запускай второй
контейнер параллельно с действующим Telegram polling.

## Непривилегированный runtime

Новый image запускает bot как фиксированный UID/GID `10001:10001` через
Dockerfile `USER`. Compose намеренно не переопределяет image user: при rollback
предыдущий image должен сохранить свой runtime contract (старый image был root).
Deployment выполняет root только в коротких одноразовых контейнерах для
backup/restore и bounded ownership migration dedicated volume. Проверка перед
стартом подтверждает, что новый runtime UID читает state и пишет в каталог;
основной контейнер нового image не root.

## Безопасная диагностика

Проверка конфигурации без вывода значений:

```bash
sshai run --body-file - sf-bot-app01 <<'SH'
set -e
cd /root/tg-jira-tasks
image=$(docker inspect --format '{{.Image}}' tg-jira-bot)
BOT_IMAGE="$image" docker compose run --rm --no-deps \
  --entrypoint python bot -m bot.config_check
SH
```

Проверка статуса контейнера (не environment):

```bash
sshai run sf-bot-app01 -- \
  docker inspect --format '{{.State.Status}} {{.RestartCount}}' tg-jira-bot
```

Никогда не используй `docker exec ... env`, `docker compose config` без
подавления вывода или `grep TELEGRAM|JIRA`: такие команды раскрывают токены.
State-проверка сообщает только compatible/incompatible и выполняется на копии;
не выводи JSON, chat ID, tracked users или event data.
