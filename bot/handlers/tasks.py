import asyncio
import logging
from collections import defaultdict

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service, JiraTask
from bot.services.notifications import notification_service

logger = logging.getLogger(__name__)

router = Router()

# Интервал по умолчанию для уведомлений (в минутах)
DEFAULT_NOTIFICATION_INTERVAL = 30

# Сообщение пользователю при ошибке Jira (детали — только в логах с exc_info)
JIRA_ERROR_MESSAGE = "⚠️ Could not reach Jira. Try again later."

# Задержка перед удалением loading-сообщения (в секундах)
LOADING_DELETE_DELAY = 5

# Удерживаем ссылки на фоновые задачи, чтобы их не собрал GC до завершения.
_pending_tasks: set[asyncio.Task] = set()


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
    line = f"- {hlink(task.key, task.url)}: {task.summary}"
    if show_assignee or show_status:
        details = []
        if show_assignee:
            details.append(task.assignee or "Unassigned")
        if show_status:
            details.append(task.status)
        line += f"\n  └ {' | '.join(details)}"
    return line


def render_grouped_by_status(
    tasks: list[JiraTask], title: str, status_order: list[str]
) -> str:
    """Группирует задачи по статусу в заданном порядке и форматирует ответ."""
    by_status: dict[str, list[JiraTask]] = defaultdict(list)
    for task in tasks:
        by_status[task.status].append(task)

    # Сначала статусы из явного порядка, затем все остальные
    statuses = [s for s in status_order if s in by_status]
    statuses.extend(s for s in by_status if s not in status_order)

    lines = [hbold(title), ""]
    for status in statuses:
        lines.append(f"\n{hbold(status)}:")
        lines.extend(format_task(task) for task in by_status[status])
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
        "/unsilent [user] — Включить звук от пользователя"
    )


@router.message(Command("inprog"))
async def cmd_inprog(message: Message) -> None:
    """Обработчик команды /inprog - показывает задачи в работе."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_my_tasks_in_progress()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No tasks in 'In Progress' status.")
        return

    lines = [hbold("My tasks in progress:"), ""]
    lines.extend(format_task(task) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("sprint"))
async def cmd_sprint(message: Message) -> None:
    """Обработчик команды /sprint - показывает задачи в спринте, сгруппированные по статусу."""
    loading_msg = await message.answer("Loading sprint tasks...")

    try:
        tasks = await jira_service.get_my_tasks_in_sprint()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No tasks found in active sprint.")
        return

    await message.answer(render_grouped_by_status(
        tasks,
        title="My sprint tasks:",
        status_order=["In Progress", "Discussion", "Hold", "Backlog", "Resolved"],
    ))


@router.message(Command("byme"))
async def cmd_byme(message: Message) -> None:
    """Обработчик команды /byme - показывает незавершённые задачи, созданные мной."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_tasks_created_by_me()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No unresolved tasks created by you (assigned to others).")
        return

    lines = [hbold("Tasks created by me (assigned to others):"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("todo"))
async def cmd_todo(message: Message) -> None:
    """Обработчик команды /todo - показывает задачи в бэклоге."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_todo_tasks()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No tasks in backlog (To Do / Backlog / Open).")
        return

    lines = [hbold("My backlog tasks:"), ""]
    lines.extend(format_task(task) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("waiting"))
async def cmd_waiting(message: Message) -> None:
    """Обработчик команды /waiting - показывает задачи в ожидании (Discussion / Hold)."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_waiting_tasks()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No tasks in Discussion / On Hold status.")
        return

    lines = [hbold("Tasks waiting for decision:"), ""]
    lines.extend(format_task(task, show_status=True) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("recent"))
async def cmd_recent(message: Message) -> None:
    """Обработчик команды /recent - показывает недавно обновлённые задачи, сгруппированные по статусу."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_recent_tasks(hours=24)
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("No tasks updated in the last 24 hours.")
        return

    await message.answer(render_grouped_by_status(
        tasks,
        title="Tasks updated in last 24h:",
        status_order=["In Progress", "Reopened", "Discussion", "On Hold", "Resolved", "Closed"],
    ))


@router.message(Command("watching"))
async def cmd_watching(message: Message) -> None:
    """Обработчик команды /watching - показывает задачи, которые я отслеживаю."""
    loading_msg = await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_watching_tasks()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    if not tasks:
        await message.answer("You are not watching any unresolved tasks (assigned to others).")
        return

    lines = [hbold("Tasks I'm watching:"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Обработчик команды /stats - показывает статистику по задачам."""
    loading_msg = await message.answer("Loading stats...")

    try:
        stats = await jira_service.get_stats()
    except Exception:
        logger.exception("Jira call failed")
        await message.answer(JIRA_ERROR_MESSAGE)
        return
    finally:
        schedule_delete(loading_msg)

    lines = [
        hbold("📊 My task statistics:"),
        "",
        f"🔵 In Progress: {stats.in_progress}",
        f"📋 Backlog: {stats.in_backlog}",
        f"✅ Resolved this week: {stats.resolved_this_week}",
        f"📌 Total open: {stats.total_assigned}",
    ]

    await message.answer("\n".join(lines))


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
                f"Example: /sync 15 (default: {DEFAULT_NOTIFICATION_INTERVAL})"
            )
            return

    # Если уже подписан - обновляем интервал и запускаем проверку
    if notification_service.is_subscribed(chat_id):
        current_interval = notification_service.get_interval()
        new_interval = interval_minutes or current_interval

        if new_interval != current_interval:
            await notification_service.update_interval(chat_id, new_interval)
            await message.answer(f"🔄 Interval updated: {current_interval} → {new_interval} min\nChecking for updates...")
        else:
            await message.answer("Checking for updates...")

        # Запускаем немедленную проверку
        await notification_service.check_now()
        return

    # Новая подписка
    if await notification_service.subscribe(chat_id, interval_minutes):
        actual_interval = interval_minutes or DEFAULT_NOTIFICATION_INTERVAL
        await message.answer(
            f"✅ Notifications enabled!\n\n"
            f"You will receive updates every {actual_interval} minutes:\n"
            "- New comments on your tasks\n"
            "- Status changes by others\n"
            "- New task assignments\n\n"
            "Use /unsync to disable."
        )
    else:
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


@router.message(Command("silent"))
async def cmd_silent(message: Message, command: CommandObject) -> None:
    """Обработчик команды /silent - включает/выключает тихий режим для пользователя."""
    target_user = command.args.strip() if command.args else None

    # Если аргумент не передан, используем имя текущего пользователя
    if not target_user:
        try:
            target_user = await jira_service.get_current_user()
            if not target_user:
                await message.answer("Could not determine your Jira username. Please specify it explicitly: /silent username")
                return
        except Exception:
            logger.exception("Failed to fetch current Jira user")
            await message.answer("⚠️ Could not determine your Jira username.")
            return

    is_silent = notification_service.is_user_silent(target_user)

    if is_silent:
        await message.answer(f"Messages from '{target_user}' are already silent (Sound OFF).")
        return

    await notification_service.mute_user(target_user)
    await message.answer(f"🔕 Sound OFF for messages from '{target_user}'.")


@router.message(Command("unsilent"))
async def cmd_unsilent(message: Message, command: CommandObject) -> None:
    """Обработчик команды /unsilent - включает звук для пользователя."""
    target_user = command.args.strip() if command.args else None

    # Если аргумент не передан, используем имя текущего пользователя
    if not target_user:
        try:
            target_user = await jira_service.get_current_user()
            if not target_user:
                await message.answer("Could not determine your Jira username. Please specify it explicitly: /unsilent username")
                return
        except Exception:
            logger.exception("Failed to fetch current Jira user")
            await message.answer("⚠️ Could not determine your Jira username.")
            return

    if not notification_service.is_user_silent(target_user):
        await message.answer(f"Messages from '{target_user}' are already audible (Sound ON).")
        return

    await notification_service.unmute_user(target_user)
    await message.answer(f"🔔 Sound ON for messages from '{target_user}'.")
