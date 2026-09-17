"""Invalid persisted state fails startup before Telegram is contacted."""
from unittest.mock import MagicMock

import pytest

import bot.main as entrypoint
from bot.services.notifications import StateLoadError


async def test_invalid_state_fails_before_bot_creation(monkeypatch):
    error = StateLoadError("Notification state could not be loaded")
    monkeypatch.setattr(entrypoint, "notification_service", MagicMock(state_load_error=error))
    bot = MagicMock()
    monkeypatch.setattr(entrypoint, "Bot", bot)
    with pytest.raises(StateLoadError) as caught:
        await entrypoint.main()
    assert caught.value is error
    bot.assert_not_called()
