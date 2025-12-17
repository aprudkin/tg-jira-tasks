from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service
from bot.services.notifications import notification_service, NotificationService

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "Jira Tasks Bot\n\n"
        "Available commands:\n"
        "/inwork - Show my tasks in progress\n"
        "/sprint - Show my tasks in active sprint\n"
        "/byme - Show unresolved tasks created by me (assigned to others)\n"
        "/sync [X] - Enable Jira notifications (every X min, default 30)\n"
        "/unsync - Disable Jira notifications"
    )


@router.message(Command("inwork"))
async def cmd_inwork(message: Message) -> None:
    """Обработчик команды /inwork - показывает задачи в работе."""
    await message.answer("Loading tasks...")

    try:
        tasks = jira_service.get_my_tasks_in_progress()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks in 'In Progress' status.")
        return

    lines = [hbold("My tasks in progress:"), ""]
    for task in tasks:
        lines.append(f"- {hlink(task.key, task.url)}: {task.summary}")

    # parse_mode берётся из DefaultBotProperties в main.py
    await message.answer("\n".join(lines))


@router.message(Command("sprint"))
async def cmd_sprint(message: Message) -> None:
    """Обработчик команды /sprint - показывает задачи в спринте, сгруппированные по статусу."""
    await message.answer("Loading sprint tasks...")

    try:
        tasks = jira_service.get_my_tasks_in_sprint()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks found in active sprint.")
        return

    # Группировка по статусу
    tasks_by_status = {}
    for task in tasks:
        if task.status not in tasks_by_status:
            tasks_by_status[task.status] = []
        tasks_by_status[task.status].append(task)

    lines = [hbold("My sprint tasks:"), ""]

    # Порядок сортировки статусов
    status_order = ["In Progress", "Discussion", "Hold", "Backlog", "Resolved"]

    # Сначала выводим статусы из предопределенного порядка
    for status in status_order:
        if status in tasks_by_status:
            lines.append(f"\n{hbold(status)}:")
            for task in tasks_by_status[status]:
                lines.append(f"- {hlink(task.key, task.url)}: {task.summary}")
            # Удаляем обработанный статус, чтобы не вывести его повторно
            del tasks_by_status[status]

    # Затем выводим оставшиеся статусы (если есть)
    for status, status_tasks in tasks_by_status.items():
        lines.append(f"\n{hbold(status)}:")
        for task in status_tasks:
            lines.append(f"- {hlink(task.key, task.url)}: {task.summary}")

    await message.answer("\n".join(lines))


@router.message(Command("byme"))
async def cmd_byme(message: Message) -> None:
    """Обработчик команды /byme - показывает незавершённые задачи, созданные мной."""
    await message.answer("Loading tasks...")

    try:
        tasks = jira_service.get_tasks_created_by_me()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No unresolved tasks created by you (assigned to others).")
        return

    lines = [hbold("Tasks created by me (assigned to others):"), ""]
    for task in tasks:
        assignee = task.assignee or "Unassigned"
        lines.append(f"- {hlink(task.key, task.url)}: {task.summary}")
        lines.append(f"  └ {assignee} ({task.status})")

    await message.answer("\n".join(lines))


@router.message(Command("sync"))
async def cmd_sync(message: Message, command: CommandObject) -> None:
    """Обработчик команды /sync - включает уведомления о событиях Jira."""
    chat_id = message.chat.id

    if notification_service.is_subscribed(chat_id):
        current_interval = notification_service.get_interval(chat_id)
        await message.answer(
            f"Notifications are already enabled (every {current_interval} min).\n"
            "Use /unsync to disable first."
        )
        return

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
                f"Example: /sync 15 (default: {NotificationService.DEFAULT_INTERVAL_MINUTES})"
            )
            return

    if notification_service.subscribe(chat_id, interval_minutes):
        actual_interval = interval_minutes or NotificationService.DEFAULT_INTERVAL_MINUTES
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

    if notification_service.unsubscribe(chat_id):
        await message.answer("🔕 Notifications disabled.")
    else:
        await message.answer("Failed to disable notifications.")
