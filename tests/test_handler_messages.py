"""Регрессии безопасных ответов команд с пользовательскими значениями."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from aiogram.filters import CommandObject
import pytest

import bot.handlers.tasks as tasks
import bot.services.jira as jira_module
from bot.services.jira import JiraService
from bot.services.notifications import Channel


def _message():
    message = MagicMock()
    message.answer = AsyncMock()
    message.chat.id = 100
    return message


def _command(name: str, args: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command=name, mention=None, args=args)


@pytest.mark.asyncio
async def test_track_usage_is_plain_valid_text():
    message = _message()
    await tasks.cmd_track(message, _command("track"))

    call = message.answer.await_args
    assert "<jira-user>" in call.kwargs["text"]
    assert call.kwargs["entities"] == []
    assert call.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_track_parse_error_is_plain_valid_text():
    message = _message()
    await tasks.cmd_track(message, _command("track", "jdoe invalid-marker"))

    call = message.answer.await_args
    assert "Не разобрал аргументы" in call.kwargs["text"]
    assert "<jira-user>" in call.kwargs["text"]
    assert call.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_untrack_usage_is_plain_valid_text():
    message = _message()
    await tasks.cmd_untrack(message, _command("untrack"))

    call = message.answer.await_args
    assert call.kwargs["text"] == "Usage: /untrack <jira-user>"
    assert call.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_silent_without_user_handles_jira_initialization_error(monkeypatch):
    """Ошибка холодного старта Jira должна давать безопасный общий ответ."""
    message = _message()

    def failed_constructor(**kwargs):
        raise RuntimeError("connection failed")

    monkeypatch.setattr(jira_module, "JIRA", failed_constructor)
    monkeypatch.setattr(tasks, "jira_service", JiraService())

    await tasks.cmd_silent(message, _command("silent"))

    message.answer.assert_awaited_once_with("⚠️ Could not determine your Jira username.")


@pytest.mark.asyncio
async def test_tracks_escapes_nothing_and_chunks_many_channels(monkeypatch):
    message = _message()
    channels = [
        Channel(
            user=f'<user-{index} & "quoted">' + "x" * 80,
            interval_minutes=15,
            emoji="<🔵>",
            last_check=datetime(2026, 1, 1, 12, 0),
        )
        for index in range(100)
    ]
    monkeypatch.setattr(tasks.notification_service, "list_channels", lambda: channels)

    await tasks.cmd_tracks(message)

    assert message.answer.await_count > 1
    text = "".join(call.kwargs["text"] for call in message.answer.await_args_list)
    assert '<user-0 & "quoted">' in text
    assert "<🔵>" in text
    for call in message.answer.await_args_list:
        assert call.kwargs["parse_mode"] is None
        assert len(call.kwargs["text"].encode("utf-16-le")) // 2 <= 4096
