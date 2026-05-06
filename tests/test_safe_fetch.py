"""Тесты для _safe_fetch helper в bot.handlers.tasks."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.tasks import _safe_fetch, _FAILED, JIRA_ERROR_MESSAGE


def _message_mock():
    """Mock aiogram.types.Message с answer-возвратом нового loading-сообщения."""
    loading_msg = MagicMock()
    loading_msg.delete = AsyncMock()
    msg = MagicMock()
    msg.answer = AsyncMock(return_value=loading_msg)
    return msg, loading_msg


@pytest.mark.asyncio
async def test_safe_fetch_returns_value_on_success():
    msg, _ = _message_mock()
    fetch = AsyncMock(return_value=["a", "b"])

    result = await _safe_fetch(msg, "Loading...", fetch)

    assert result == ["a", "b"]
    assert msg.answer.await_args_list[0].args == ("Loading...",)


@pytest.mark.asyncio
async def test_safe_fetch_returns_failed_sentinel_on_exception():
    msg, _ = _message_mock()
    fetch = AsyncMock(side_effect=RuntimeError("jira down"))

    result = await _safe_fetch(msg, "Loading...", fetch)

    assert result is _FAILED
    # Loading + JIRA_ERROR_MESSAGE
    assert any(c.args == (JIRA_ERROR_MESSAGE,) for c in msg.answer.await_args_list)


@pytest.mark.asyncio
async def test_safe_fetch_does_not_leak_exception_to_user():
    """Сообщение пользователю — generic, без raw exception."""
    msg, _ = _message_mock()
    fetch = AsyncMock(side_effect=RuntimeError("super-secret-token=xyz"))

    await _safe_fetch(msg, "Loading...", fetch)

    all_responses = [c.args[0] for c in msg.answer.await_args_list]
    for response in all_responses:
        assert "super-secret-token" not in response
        assert "xyz" not in response
