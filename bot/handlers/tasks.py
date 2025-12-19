from collections import defaultdict

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service, JiraTask
from bot.services.notifications import notification_service

router = Router()

# Интервал по умолчанию для уведомлений (в минутах)
DEFAULT_NOTIFICATION_INTERVAL = 30


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


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "Jira Tasks Bot\n\n"
        "Available commands:\n"
        "/inwork - Tasks in progress\n"
        "/todo - Tasks in backlog\n"
        "/sprint - Tasks in active sprint\n"
        "/recent - Tasks updated in last 24h\n"
        "/watching - Tasks I'm watching\n"
        "/byme - Tasks created by me (assigned to others)\n"
        "/stats - Task statistics\n"
        "/sync [X] - Enable notifications (every X min, default 30)\n"
        "/unsync - Disable notifications\n"
        "/silent [user] - Mute notifications from user (default: self)\n"
        "/unsilent [user] - Unmute notifications from user (default: self)"
    )


@router.message(Command("inwork"))
async def cmd_inwork(message: Message) -> None:
    """Обработчик команды /inwork - показывает задачи в работе."""
    await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_my_tasks_in_progress()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks in 'In Progress' status.")
        return

    lines = [hbold("My tasks in progress:"), ""]
    lines.extend(format_task(task) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("sprint"))
async def cmd_sprint(message: Message) -> None:
    """Обработчик команды /sprint - показывает задачи в спринте, сгруппированные по статусу."""
    await message.answer("Loading sprint tasks...")

    try:
        tasks = await jira_service.get_my_tasks_in_sprint()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks found in active sprint.")
        return

    # Группировка по статусу с defaultdict
    tasks_by_status: dict[str, list[JiraTask]] = defaultdict(list)
    for task in tasks:
        tasks_by_status[task.status].append(task)

    lines = [hbold("My sprint tasks:"), ""]

    # Порядок сортировки статусов
    status_order = ["In Progress", "Discussion", "Hold", "Backlog", "Resolved"]
    # Собираем все статусы: сначала из порядка, потом остальные
    all_statuses = [s for s in status_order if s in tasks_by_status]
    all_statuses.extend(s for s in tasks_by_status if s not in status_order)

    for status in all_statuses:
        lines.append(f"\n{hbold(status)}:")
        lines.extend(format_task(task) for task in tasks_by_status[status])

    await message.answer("\n".join(lines))


@router.message(Command("byme"))
async def cmd_byme(message: Message) -> None:
    """Обработчик команды /byme - показывает незавершённые задачи, созданные мной."""
    await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_tasks_created_by_me()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No unresolved tasks created by you (assigned to others).")
        return

    lines = [hbold("Tasks created by me (assigned to others):"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("todo"))
async def cmd_todo(message: Message) -> None:
    """Обработчик команды /todo - показывает задачи в бэклоге."""
    await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_todo_tasks()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks in backlog (To Do / Backlog / Open).")
        return

    lines = [hbold("My backlog tasks:"), ""]
    lines.extend(format_task(task) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("recent"))
async def cmd_recent(message: Message) -> None:
    """Обработчик команды /recent - показывает недавно обновлённые задачи, сгруппированные по статусу."""
    await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_recent_tasks(hours=24)
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("No tasks updated in the last 24 hours.")
        return

    # Группировка по статусу
    tasks_by_status: dict[str, list[JiraTask]] = defaultdict(list)
    for task in tasks:
        tasks_by_status[task.status].append(task)

    lines = [hbold("Tasks updated in last 24h:"), ""]

    # Порядок статусов по важности
    status_order = ["In Progress", "Reopened", "Discussion", "On Hold", "Resolved", "Closed"]
    # Собираем все статусы: сначала из порядка, потом остальные
    all_statuses = [s for s in status_order if s in tasks_by_status]
    all_statuses.extend(s for s in tasks_by_status if s not in status_order)

    for status in all_statuses:
        lines.append(f"\n{hbold(status)}:")
        lines.extend(format_task(task) for task in tasks_by_status[status])

    await message.answer("\n".join(lines))


@router.message(Command("watching"))
async def cmd_watching(message: Message) -> None:
    """Обработчик команды /watching - показывает задачи, которые я отслеживаю."""
    await message.answer("Loading tasks...")

    try:
        tasks = await jira_service.get_watching_tasks()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

    if not tasks:
        await message.answer("You are not watching any unresolved tasks (assigned to others).")
        return

    lines = [hbold("Tasks I'm watching:"), ""]
    lines.extend(format_task(task, show_status=True, show_assignee=True) for task in tasks)

    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Обработчик команды /stats - показывает статистику по задачам."""
    await message.answer("Loading stats...")

    try:
        stats = await jira_service.get_stats()
    except Exception as e:
        await message.answer(f"Error connecting to Jira: {e}")
        return

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
        except Exception as e:
            await message.answer(f"Error fetching current user: {e}")
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
        except Exception as e:
            await message.answer(f"Error fetching current user: {e}")
            return

    if not notification_service.is_user_silent(target_user):
        await message.answer(f"Messages from '{target_user}' are already audible (Sound ON).")
        return

    await notification_service.unmute_user(target_user)
    await message.answer(f"🔔 Sound ON for messages from '{target_user}'.")
