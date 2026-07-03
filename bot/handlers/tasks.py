import asyncio
import logging
import re
from collections import defaultdict

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold

from bot import status
from bot.render import issue_ref
from bot.services.jira import jira_service, JiraTask
from bot.services.notifications import notification_service, PERSONAL

logger = logging.getLogger(__name__)

router = Router()

# Сообщение пользователю при ошибке Jira (детали — только в логах с exc_info)
JIRA_ERROR_MESSAGE = "⚠️ Could not reach Jira. Try again later."

# Текст loading-сообщения для большинства команд
LOADING_TASKS = "Loading tasks..."

# Лимит длины сообщения Telegram — 4096 UTF-16 code units. Берём с запасом
# на накладные HTML-теги и эмодзи.
TG_MESSAGE_CHUNK_SIZE = 4000

# Задержка перед удалением loading-сообщения (в секундах)
LOADING_DELETE_DELAY = 5

# Удерживаем ссылки на фоновые задачи, чтобы их не собрал GC до завершения.
_pending_tasks: set[asyncio.Task] = set()


def _is_marker(token: str) -> bool:
    """Прагматичная проверка «это маркер-эмодзи»: короткий кластер без ASCII-букв/цифр.

    Идеальный emoji-детект не гоняем — достаточно отсечь текст и числа.
    """
    if not token or len(token) > 8:
        return False
    return re.search(r"[A-Za-z0-9]", token) is None


def parse_track_args(args: str) -> tuple[str, str | None, int | None]:
    """Разбирает аргументы /track: '<user> [эмодзи] [интервал]' (хвост — в любом порядке).

    Токены после user определяются по типу: число → интервал (>=1), иначе → маркер.
    Возвращает (user, emoji|None, interval|None). Бросает ValueError при пустом вводе,
    нулевом/отрицательном интервале или нераспознанном (не-эмодзи) аргументе.
    """
    tokens = args.split()
    if not tokens:
        raise ValueError("empty /track args: user required")

    user = tokens[0]
    emoji: str | None = None
    interval: int | None = None
    for token in tokens[1:]:
        if token.isdigit():
            value = int(token)
            if value < 1:
                raise ValueError("interval must be at least 1 minute")
            interval = value
        elif emoji is None and _is_marker(token):
            emoji = token
        else:
            raise ValueError(f"unrecognized argument: {token}")
    return user, emoji, interval


def schedule_delete(msg: Message, delay: float = LOADING_DELETE_DELAY) -> None:
    """Планирует удаление сообщения через указанное время."""
    async def _delete_later() -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            # Игнорируем ошибки удаления (сообщение уже удалено и т.д.)
            pass
    task = asyncio.create_task(_delete_later())
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


def format_task(task: JiraTask, show_status: bool = False, show_assignee: bool = False) -> str:
    """Форматирует задачу для отображения в Telegram."""
    line = f"- {issue_ref(task.key, task.url, task.summary)}"
    if show_assignee or show_status:
        details = []
        if show_assignee:
            details.append(task.assignee or "Unassigned")
        if show_status:
            details.append(task.status)
        line += f"\n  └ {' | '.join(details)}"
    return line


class _Failed:
    """Маркер: fetch упал, _safe_fetch уже ответил пользователю и залогировал."""

# Singleton, проверяется через `is _FAILED`
_FAILED = _Failed()


async def _answer_chunked(message: Message, text: str) -> None:
    """Отправляет text, при необходимости бьёт на чанки по '\\n'.

    Telegram ограничивает сообщение 4096 символами; при превышении aiogram
    бросает TelegramBadRequest. Помогает командам с большим числом задач
    (/sprint, /recent, /watching).
    """
    if len(text) <= TG_MESSAGE_CHUNK_SIZE:
        await message.answer(text)
        return

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Одна строка длиннее лимита — режем принудительно (edge case).
        while len(line) > TG_MESSAGE_CHUNK_SIZE:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:TG_MESSAGE_CHUNK_SIZE])
            line = line[TG_MESSAGE_CHUNK_SIZE:]
        sep = "\n" if current else ""
        if len(current) + len(sep) + len(line) <= TG_MESSAGE_CHUNK_SIZE:
            current += sep + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)

    for chunk in chunks:
        await message.answer(chunk)


async def _safe_fetch(message: Message, loading_text: str, fetch):
    """Шлёт loading-сообщение, ждёт fetch, удаляет loader. При исключении
    логирует и отвечает пользователю generic-сообщением; возвращает _FAILED.

    Caller: `result = await _safe_fetch(...); if result is _FAILED: return`.
    """
    loading_msg = await message.answer(loading_text)
    try:
        return await fetch()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return _FAILED
    finally:
        schedule_delete(loading_msg)


def render_grouped_by_status(tasks: list[JiraTask], title: str) -> str:
    """Группирует задачи по статусу в каноническом порядке (status.ORDER) и форматирует ответ.

    Неизвестные статусы идут последними (по алфавиту — для детерминизма вывода).
    """
    by_status: dict[str, list[JiraTask]] = defaultdict(list)
    for task in tasks:
        by_status[task.status].append(task)

    rank = {name: i for i, name in enumerate(status.ORDER)}
    ordered = sorted(by_status, key=lambda s: (rank.get(s, len(status.ORDER)), s))

    lines = [hbold(title), ""]
    for name in ordered:
        lines.append(f"\n{hbold(name)}:")
        lines.extend(format_task(task) for task in by_status[name])
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "🎫 <b>Jira Tasks Bot</b>\n\n"
        "📋 <b>Просмотр задач:</b>\n"
        "/inprog — Задачи в работе (In Progress)\n"
        "/todo — Задачи в бэклоге\n"
        "/waiting — Задачи в ожидании (Discussion / Hold)\n"
        "/sprint — Задачи в активном спринте\n"
        "/recent — Обновлённые за 24ч\n"
        "/watching — Задачи, которые я отслеживаю\n"
        "/byme — Созданные мной (назначены другим)\n"
        "/stats — Статистика по задачам\n\n"
        "🔔 <b>Уведомления:</b>\n"
        "/sync [мин] — Включить уведомления (по умолчанию 30 мин)\n"
        "/unsync — Отключить уведомления\n"
        "/silent [user] — Отключить звук от пользователя\n"
        "/unsilent [user] — Включить звук от пользователя\n\n"
        "👥 <b>Слежение за коллегами:</b>\n"
        "/track &lt;user&gt; [эмодзи] [мин] — Следить за задачами коллеги\n"
        "/untrack &lt;user&gt; — Перестать следить\n"
        "/tracks — Список отслеживаемых каналов"
    )


@router.message(Command("inprog"))
async def cmd_inprog(message: Message) -> None:
    """Обработчик команды /inprog - показывает задачи в работе."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_my_tasks_in_progress)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No tasks in 'In Progress' status.")
        return

    lines = [hbold("My tasks in progress:"), ""]
    lines.extend(format_task(task) for task in tasks)
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("sprint"))
async def cmd_sprint(message: Message) -> None:
    """Обработчик команды /sprint - показывает задачи в спринте, сгруппированные по статусу."""
    tasks = await _safe_fetch(message, "Loading sprint tasks...", jira_service.get_my_tasks_in_sprint)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No tasks found in active sprint.")
        return

    await _answer_chunked(message, render_grouped_by_status(tasks, title="My sprint tasks:"))


@router.message(Command("byme"))
async def cmd_byme(message: Message) -> None:
    """Обработчик команды /byme - показывает незавершённые задачи, созданные мной."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_tasks_created_by_me)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No unresolved tasks created by you (assigned to others).")
        return

    lines = [hbold("Tasks created by me (assigned to others):"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("todo"))
async def cmd_todo(message: Message) -> None:
    """Обработчик команды /todo - показывает задачи в бэклоге."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_todo_tasks)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No tasks in backlog (To Do / Backlog / Open).")
        return

    lines = [hbold("My backlog tasks:"), ""]
    lines.extend(format_task(task) for task in tasks)
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("waiting"))
async def cmd_waiting(message: Message) -> None:
    """Обработчик команды /waiting - показывает задачи в ожидании (Discussion / Hold)."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_waiting_tasks)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No tasks in Discussion / On Hold status.")
        return

    lines = [hbold("Tasks waiting for decision:"), ""]
    lines.extend(format_task(task, show_status=True) for task in tasks)
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("recent"))
async def cmd_recent(message: Message) -> None:
    """Обработчик команды /recent - показывает недавно обновлённые задачи, сгруппированные по статусу."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_recent_tasks)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("No tasks updated in the last 24 hours.")
        return

    await _answer_chunked(message, render_grouped_by_status(tasks, title="Tasks updated in last 24h:"))


@router.message(Command("watching"))
async def cmd_watching(message: Message) -> None:
    """Обработчик команды /watching - показывает задачи, которые я отслеживаю."""
    tasks = await _safe_fetch(message, LOADING_TASKS, jira_service.get_watching_tasks)
    if tasks is _FAILED:
        return
    if not tasks:
        await message.answer("You are not watching any unresolved tasks (assigned to others).")
        return

    lines = [hbold("Tasks I'm watching:"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Обработчик команды /stats - показывает статистику по задачам."""
    stats = await _safe_fetch(message, "Loading stats...", jira_service.get_stats)
    if stats is _FAILED:
        return

    lines = [
        hbold("📊 My task statistics:"),
        "",
        f"🔵 In Progress: {stats.in_progress}",
        f"📋 Backlog: {stats.in_backlog}",
        f"✅ Resolved this week: {stats.resolved_this_week}",
        f"📌 Total open: {stats.total_assigned}",
    ]
    await _answer_chunked(message, "\n".join(lines))


@router.message(Command("sync"))
async def cmd_sync(message: Message, command: CommandObject) -> None:
    """Обработчик команды /sync - включает или обновляет уведомления о событиях Jira."""
    chat_id = message.chat.id

    # Парсим интервал из аргумента команды
    interval_minutes: int | None = None
    if command.args:
        try:
            interval_minutes = int(command.args.strip())
            if interval_minutes < 1:
                await message.answer("Interval must be at least 1 minute.")
                return
        except ValueError:
            await message.answer(
                "Invalid interval. Usage: /sync [minutes]\n"
                f"Example: /sync 15 (default: {notification_service.DEFAULT_INTERVAL_MINUTES})"
            )
            return

    outcome = await notification_service.enable_personal(chat_id, interval_minutes)

    if outcome.status == "interval_changed":
        await message.answer(
            f"🔄 Interval updated: {outcome.old_interval} → {outcome.interval} min\nChecking for updates..."
        )
        # check_now — ПОСЛЕ подтверждения, иначе уведомления уйдут раньше него
        await notification_service.check_now()
    elif outcome.status == "unchanged":
        await message.answer("Checking for updates...")
        await notification_service.check_now()
    elif outcome.status == "enabled":
        await message.answer(
            f"✅ Notifications enabled!\n\n"
            f"You will receive updates every {outcome.interval} minutes:\n"
            "- New comments on your tasks\n"
            "- Status changes by others\n"
            "- New task assignments\n\n"
            "Use /unsync to disable."
        )
    else:  # chat_busy
        await message.answer("Failed to enable notifications.")


@router.message(Command("unsync"))
async def cmd_unsync(message: Message) -> None:
    """Обработчик команды /unsync - отключает уведомления."""
    chat_id = message.chat.id

    if not notification_service.is_subscribed(chat_id):
        await message.answer("Notifications are not enabled.")
        return

    if await notification_service.unsubscribe(chat_id):
        await message.answer("🔕 Notifications disabled.")
    else:
        await message.answer("Failed to disable notifications.")


TRACK_USAGE = "Usage: /track <jira-user> [эмодзи] [интервал]\nПример: /track jdoe 🔵 15"


@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject) -> None:
    """Обработчик /track - поднимает независимый канал слежения за задачами коллеги."""
    if not command.args:
        await message.answer(TRACK_USAGE)
        return

    try:
        user, emoji, interval = parse_track_args(command.args)
    except ValueError:
        await message.answer(f"Не разобрал аргументы.\n{TRACK_USAGE}")
        return

    if user == PERSONAL:
        await message.answer("Это служебное имя личного канала. Для своих задач — /sync.")
        return

    outcome = await notification_service.track_colleague(message.chat.id, user, emoji, interval)

    if outcome.status == "chat_busy":
        await message.answer("Бот уже привязан к другому чату.")
        return
    if outcome.status == "probe_failed":
        await message.answer(
            f"⚠️ Не могу прочитать задачи '{user}'. Проверь Jira-имя и права бота."
        )
        return

    channel = outcome.channel
    tail = (
        "сейчас 0 назначенных задач" if outcome.assigned_count == 0
        else f"{outcome.assigned_count} назначенных задач"
    )
    await message.answer(
        f"{channel.emoji} Слежу за '{user}' ({tail}). Интервал {channel.interval_minutes} мин."
    )
    # Немедленная первая проверка канала — ПОСЛЕ подтверждающего ответа
    await notification_service.check_now(user)


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject) -> None:
    """Обработчик /untrack - убирает канал слежения за коллегой."""
    if not command.args or not command.args.strip():
        await message.answer("Usage: /untrack <jira-user>")
        return

    user = command.args.strip().split()[0]
    if await notification_service.remove_channel(user):
        await message.answer(f"🚫 Больше не слежу за '{user}'.")
    else:
        await message.answer(f"'{user}' не отслеживается.")


@router.message(Command("tracks"))
async def cmd_tracks(message: Message) -> None:
    """Обработчик /tracks - список активных каналов слежения за коллегами."""
    channels = notification_service.list_channels()
    colleagues = [c for c in channels if not c.is_personal]
    if not colleagues:
        await message.answer(
            "Нет отслеживаемых коллег.\nДобавить: /track <jira-user> [эмодзи] [интервал]"
        )
        return

    lines = [hbold("Отслеживаемые каналы:"), ""]
    for channel in channels:
        if channel.is_personal:
            lines.append(f"👤 <b>ты</b> (личный) — интервал {channel.interval_minutes} мин")
        else:
            last = channel.last_check.strftime("%H:%M") if channel.last_check else "—"
            lines.append(
                f"{channel.emoji} <b>{channel.user}</b> — интервал {channel.interval_minutes} мин, "
                f"проверен {last} UTC"
            )
    await message.answer("\n".join(lines))


async def _resolve_target_user(message: Message, command: CommandObject) -> str | None:
    """Возвращает имя Jira-пользователя из аргумента команды или текущего пользователя.

    При ошибке отвечает пользователю сам и возвращает None — caller просто делает return.
    """
    if command.args and command.args.strip():
        return command.args.strip()
    try:
        user = await jira_service.get_current_user()
    except Exception:
        logger.exception("Failed to fetch current Jira user")
        await message.answer("⚠️ Could not determine your Jira username.")
        return None
    if not user:
        await message.answer(
            f"Could not determine your Jira username. "
            f"Please specify it explicitly: {command.prefix}{command.command} username"
        )
        return None
    return user


@router.message(Command("silent"))
async def cmd_silent(message: Message, command: CommandObject) -> None:
    """Обработчик команды /silent - включает/выключает тихий режим для пользователя."""
    target_user = await _resolve_target_user(message, command)
    if target_user is None:
        return

    if notification_service.is_user_silent(target_user):
        await message.answer(f"Messages from '{target_user}' are already silent (Sound OFF).")
        return

    await notification_service.mute_user(target_user)
    await message.answer(f"🔕 Sound OFF for messages from '{target_user}'.")


@router.message(Command("unsilent"))
async def cmd_unsilent(message: Message, command: CommandObject) -> None:
    """Обработчик команды /unsilent - включает звук для пользователя."""
    target_user = await _resolve_target_user(message, command)
    if target_user is None:
        return

    if not notification_service.is_user_silent(target_user):
        await message.answer(f"Messages from '{target_user}' are already audible (Sound ON).")
        return

    await notification_service.unmute_user(target_user)
    await message.answer(f"🔔 Sound ON for messages from '{target_user}'.")
