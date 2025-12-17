from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "Jira Tasks Bot\n\n"
        "Available commands:\n"
        "/inwork - Show my tasks in progress\n"
        "/sprint - Show my tasks in active sprint"
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

    for status, status_tasks in tasks_by_status.items():
        lines.append(f"\n{hbold(status)}:")
        for task in status_tasks:
            lines.append(f"- {hlink(task.key, task.url)}: {task.summary}")

    await message.answer("\n".join(lines))
