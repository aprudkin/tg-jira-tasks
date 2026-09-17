"""Intervals are bounded before handlers call the notification service."""
from unittest.mock import AsyncMock, MagicMock

from aiogram.filters import CommandObject
import pytest

import bot.handlers.tasks as tasks
from bot.intervals import validate_interval


@pytest.mark.parametrize("value", [0, -1, 1441, 10**400, True, False, 1.0, "30", None])
def test_interval_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_interval(value)


@pytest.mark.parametrize("value", [1, 30, 1440])
def test_interval_accepts_boundaries(value):
    assert validate_interval(value) == value
    assert tasks.parse_track_args(f"alice {value}") == ("alice", None, value)


@pytest.mark.parametrize("argument", ["0", "-1", "1441", "9" * 400, "9" * 5000, "1.5", "nope"])
@pytest.mark.parametrize("command", ["sync", "track"])
async def test_invalid_interval_never_reaches_service(monkeypatch, argument, command):
    service = MagicMock()
    service.DEFAULT_INTERVAL_MINUTES = 30
    service.enable_personal = AsyncMock()
    service.track_colleague = AsyncMock()
    monkeypatch.setattr(tasks, "notification_service", service)
    message = MagicMock()
    message.answer = AsyncMock()
    args = argument if command == "sync" else f"alice {argument}"
    await getattr(tasks, f"cmd_{command}")(
        message, CommandObject(prefix="/", command=command, args=args)
    )
    message.answer.assert_awaited_once()
    service.enable_personal.assert_not_awaited()
    service.track_colleague.assert_not_awaited()


@pytest.mark.parametrize("value", [1, 1440])
async def test_sync_accepts_boundaries(monkeypatch, value):
    service = MagicMock()
    service.enable_personal = AsyncMock(return_value=MagicMock(status="chat_busy"))
    monkeypatch.setattr(tasks, "notification_service", service)
    message = MagicMock()
    message.chat.id = 100
    message.answer = AsyncMock()
    await tasks.cmd_sync(message, CommandObject(prefix="/", command="sync", args=str(value)))
    service.enable_personal.assert_awaited_once_with(100, value)
