"""Mock-based тесты для NotificationService._check_notifications.

Подмена jira_service.get_events_since и Bot.send_message — вся логика
дедупликации, очистки при close и обновления last_check проверяется
без сети.
"""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots
from bot.services.jira import JiraEvent


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Свежий NotificationService с изолированным state_file."""
    from bot.config import settings
    monkeypatch.setattr(settings, "state_file", tmp_path / "sync_state.json")
    return tmp_path


@pytest.fixture
def svc(isolated_state):
    """NotificationService с подписанным chat_id и mock-ботом."""
    s = nots.NotificationService()
    s._bot = AsyncMock()
    s._chat_id = 555
    s._last_check = datetime(2026, 1, 1, 12, 0, 0)
    return s


def _evt(issue_key: str, event_id: str, event_type: str = "comment", to_status: str | None = None) -> JiraEvent:
    return JiraEvent(
        issue_key=issue_key,
        issue_summary="s",
        issue_url=f"u/{issue_key}",
        event_type=event_type,
        author="bob",
        author_id="bob",
        details="d",
        id=event_id,
        to_status=to_status,
    )


@pytest.mark.asyncio
async def test_first_check_sends_all_events(svc, monkeypatch):
    events = [_evt("X-1", "c1"), _evt("X-1", "c2")]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    assert svc._bot.send_message.await_count == 2
    assert svc._processed_events["X-1"] == {"c1", "c2"}


@pytest.mark.asyncio
async def test_dedup_skips_already_processed(svc, monkeypatch):
    svc._processed_events["X-1"] = {"c1"}
    events = [_evt("X-1", "c1"), _evt("X-1", "c2")]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    assert svc._bot.send_message.await_count == 1
    assert svc._processed_events["X-1"] == {"c1", "c2"}


@pytest.mark.asyncio
async def test_close_status_clears_dedup_history(svc, monkeypatch):
    events = [
        _evt("X-1", "c1"),
        _evt("X-1", "s1", event_type="status_change", to_status="Done"),
    ]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    # После Done вся история X-1 должна быть очищена
    assert "X-1" not in svc._processed_events


@pytest.mark.asyncio
async def test_reopen_does_not_clear_dedup_history(svc, monkeypatch):
    """Регрессия: substring-проверка раньше ловила Resolved → Reopened."""
    events = [
        _evt("X-1", "c1"),
        _evt("X-1", "s1", event_type="status_change", to_status="Reopened"),
    ]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    assert "X-1" in svc._processed_events
    assert {"c1", "s1"}.issubset(svc._processed_events["X-1"])


@pytest.mark.asyncio
async def test_last_check_advances_even_when_no_events(svc, monkeypatch):
    initial = svc._last_check
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=[]),
    )

    await svc._check_notifications()

    assert svc._last_check is not None
    assert svc._last_check > initial
    svc._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_skipped_without_subscription(svc, monkeypatch):
    svc._chat_id = None
    mock_get = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        mock_get,
    )

    await svc._check_notifications()

    mock_get.assert_not_awaited()
    svc._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_rate_limit_triggers_retry(svc, monkeypatch):
    """При TelegramRetryAfter ждём retry_after секунд и повторяем send_message."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    sleep_calls: list[float] = []
    real_sleep = nots.asyncio.sleep
    async def fake_sleep(d):
        sleep_calls.append(d)
        # Не блокируем тест на реальные секунды
        await real_sleep(0)
    monkeypatch.setattr("bot.services.notifications.asyncio.sleep", fake_sleep)

    call_count = 0
    async def flaky_send(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TelegramRetryAfter(method=SendMessage(chat_id=0, text=""), message="slow", retry_after=7)
    svc._bot.send_message = AsyncMock(side_effect=flaky_send)

    events = [_evt("X-1", "c1")]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    assert call_count == 2  # первый — 429, второй — успех
    assert 7 in sleep_calls
    assert svc._processed_events["X-1"] == {"c1"}


@pytest.mark.asyncio
async def test_telegram_rate_limit_gives_up_after_one_retry(svc, monkeypatch):
    """После двух подряд 429 событие дропается, чтобы не висеть."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    real_sleep = nots.asyncio.sleep
    async def fake_sleep(d):
        await real_sleep(0)
    monkeypatch.setattr("bot.services.notifications.asyncio.sleep", fake_sleep)

    async def always_429(*a, **kw):
        raise TelegramRetryAfter(method=SendMessage(chat_id=0, text=""), message="slow", retry_after=1)
    svc._bot.send_message = AsyncMock(side_effect=always_429)

    events = [_evt("X-1", "c1")]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    # 2 attempt'а, не больше (no infinite loop)
    assert svc._bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_silent_user_disables_notification_sound(svc, monkeypatch):
    svc._silent_users = {"bob"}
    events = [_evt("X-1", "c1")]
    monkeypatch.setattr(
        "bot.services.notifications.jira_service.get_events_since",
        AsyncMock(return_value=events),
    )

    await svc._check_notifications()

    call = svc._bot.send_message.await_args
    assert call.kwargs["disable_notification"] is True
