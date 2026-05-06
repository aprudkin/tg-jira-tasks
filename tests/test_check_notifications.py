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
