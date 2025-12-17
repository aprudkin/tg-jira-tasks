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
        "/opened - Show my tasks in progress"
    )


@router.message(Command("opened"))
async def cmd_opened(message: Message) -> None:
    """Обработчик команды /opened - показывает задачи в работе."""
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
