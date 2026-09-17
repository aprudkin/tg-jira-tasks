"""Every state-changing command reports persistence failure without success/checks."""
from unittest.mock import AsyncMock, MagicMock

from aiogram.filters import CommandObject
import pytest

import bot.handlers.tasks as tasks
from bot.services.notifications import StateLoadError, StateSaveError


@pytest.mark.parametrize(
    "command,method,args",
    [
        ("sync", "enable_personal", "15"),
        ("unsync", "unsubscribe", None),
        ("track", "track_colleague", "alice"),
        ("untrack", "remove_channel", "alice"),
        ("silent", "mute_user", "alice"),
        ("unsilent", "unmute_user", "alice"),
    ],
)
@pytest.mark.parametrize(
    "error_type,expected_message",
    [
        (StateSaveError, tasks.STATE_SAVE_ERROR_MESSAGE),
        (StateLoadError, tasks.STATE_LOAD_ERROR_MESSAGE),
    ],
)
async def test_state_save_error_is_reported(
    monkeypatch, command, method, args, error_type, expected_message
):
    service = MagicMock()
    service.is_subscribed.return_value = True
    service.is_user_silent.return_value = command == "unsilent"
    service.check_now = AsyncMock()
    mutation = AsyncMock(side_effect=error_type("private disk details"))
    setattr(service, method, mutation)
    monkeypatch.setattr(tasks, "notification_service", service)
    message = MagicMock()
    message.chat.id = 100
    message.answer = AsyncMock()
    handler = getattr(tasks, f"cmd_{command}")
    if command == "unsync":
        await handler(message)
    else:
        await handler(message, CommandObject(prefix="/", command=command, args=args))
    mutation.assert_awaited_once()
    message.answer.assert_awaited_once_with(expected_message)
    service.check_now.assert_not_awaited()
