"""Швы №2 (управление каналами) и №1 (дедуп на канал → 2 уведомления на общем тикете)."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots
from bot.services.jira import JiraEvent


@pytest.fixture
def svc(state_path, fake_jira):
    """Сервис с привязанным чатом, без бота (фоновые задачи не поднимаются)."""
    s = nots.NotificationService(jira=fake_jira, state_file=state_path)
    s._chat_id = 100
    return s


def _evt(issue_key: str, event_id: str) -> JiraEvent:
    return JiraEvent(
        issue_key=issue_key, issue_summary="s", issue_url=f"u/{issue_key}",
        event_type="comment", author="bob", author_id="bob", details="d", id=event_id,
    )


# ---- Шов №2: управление каналами -----------------------------------------

@pytest.mark.asyncio
async def test_add_channel_creates_with_given_fields(svc):
    ch = await svc.add_channel("jdoe", "🔵", 15)
    assert ch.user == "jdoe"
    assert ch.emoji == "🔵"
    assert ch.interval_minutes == 15
    assert svc.get_channel("jdoe") is ch


@pytest.mark.asyncio
async def test_add_channel_is_idempotent_update(svc):
    await svc.add_channel("jdoe", "🔵", 15)
    svc.get_channel("jdoe").processed_events["ABC-1"] = {"c1"}

    await svc.add_channel("jdoe", "🟢", 20)

    channels = [c for c in svc.list_channels() if c.user == "jdoe"]
    assert len(channels) == 1  # не дубль
    assert channels[0].emoji == "🟢"
    assert channels[0].interval_minutes == 20
    # дедуп сохранён при обновлении
    assert channels[0].processed_events["ABC-1"] == {"c1"}


@pytest.mark.asyncio
async def test_add_channel_auto_marker_is_distinct(svc):
    a = await svc.add_channel("alice")
    b = await svc.add_channel("bob")
    assert a.emoji is not None and b.emoji is not None
    assert a.emoji != b.emoji
    assert a.emoji in nots.MARKER_PALETTE


@pytest.mark.asyncio
async def test_remove_channel(svc):
    await svc.add_channel("jdoe", "🔵", 15)
    assert await svc.remove_channel("jdoe") is True
    assert svc.get_channel("jdoe") is None


@pytest.mark.asyncio
async def test_remove_personal_via_remove_channel_refused(svc):
    svc._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=30)
    assert await svc.remove_channel(nots.PERSONAL) is False
    assert nots.PERSONAL in svc._channels


@pytest.mark.asyncio
async def test_removing_last_channel_clears_chat(svc):
    await svc.add_channel("jdoe", "🔵", 15)
    await svc.remove_channel("jdoe")
    assert svc._chat_id is None


@pytest.mark.asyncio
async def test_list_channels_personal_first(svc):
    svc._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=30)
    await svc.add_channel("zoe", "🔵", 15)
    await svc.add_channel("amy", "🟢", 15)
    order = [c.user for c in svc.list_channels()]
    assert order[0] == nots.PERSONAL
    assert order[1:] == ["amy", "zoe"]  # коллеги по имени


# ---- Шов №1: дедуп на канал → 2 уведомления на общем тикете (ADR-0002) ----

@pytest.mark.asyncio
async def test_shared_issue_notifies_each_channel_independently(state_path, fake_jira):
    s = nots.NotificationService(jira=fake_jira, state_file=state_path)
    s._bot = AsyncMock()
    s._chat_id = 100
    now = datetime(2026, 1, 1, 12, 0, 0)
    s._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=30, last_check=now)
    s._channels["jdoe"] = nots.Channel(user="jdoe", interval_minutes=15, emoji="🔵", last_check=now)

    # Один и тот же комментарий на общем тикете виден обоим каналам
    fake_jira.get_events_since.return_value = [_evt("ABC-1", "comment_1")]

    await s.check_now(nots.PERSONAL)
    await s.check_now("jdoe")

    # Дедуп на канал → отправлено дважды, каждый канал завёл свой ID независимо
    assert s._bot.send_message.await_count == 2
    assert "comment_1" in s._channels[nots.PERSONAL].processed_events["ABC-1"]
    assert "comment_1" in s._channels["jdoe"].processed_events["ABC-1"]
    # Уведомление канала коллеги несёт его маркер
    last_text = s._bot.send_message.await_args.args[1]
    assert "🔵" in last_text
