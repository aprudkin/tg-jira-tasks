"""Швы №2 (управление каналами) и №1 (дедуп на канал → 2 уведомления на общем тикете)."""
import asyncio
import threading
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
    channel = svc.get_channel("jdoe")
    channel.processed_events["ABC-1"] = {"c1"}
    unread_cursor = channel.last_check

    await svc.add_channel("jdoe", "🟢", 20)

    channels = [c for c in svc.list_channels() if c.user == "jdoe"]
    assert len(channels) == 1  # не дубль
    assert channels[0].emoji == "🟢"
    assert channels[0].interval_minutes == 20
    # дедуп сохранён при обновлении
    assert channels[0].processed_events["ABC-1"] == {"c1"}
    assert channels[0].last_check == unread_cursor


@pytest.mark.asyncio
async def test_add_channel_auto_marker_is_distinct(svc):
    a = await svc.add_channel("alice")
    b = await svc.add_channel("bob")
    assert a.emoji is not None and b.emoji is not None
    assert a.emoji != b.emoji
    assert a.emoji in nots.MARKER_PALETTE


@pytest.mark.asyncio
async def test_add_channel_write_failure_keeps_memory_and_json_unchanged(
    svc, state_path, monkeypatch
):
    await svc._save_state()
    previous_json = state_path.read_text()

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await svc.add_channel("jdoe", "🔵", 15)

    assert svc.get_channel("jdoe") is None
    assert state_path.read_text() == previous_json
    assert nots.NotificationService(state_file=state_path).get_channel("jdoe") is None


@pytest.mark.asyncio
async def test_add_channel_write_failure_does_not_start_background_task(
    state_path, fake_jira, monkeypatch
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)
    service._chat_id = 100
    service._bot = AsyncMock()

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await service.add_channel("jdoe", "🔵", 15)
    await service.stop()

    assert service.get_channel("jdoe") is None
    assert "jdoe" not in service._tasks


@pytest.mark.asyncio
async def test_existing_channel_write_failure_preserves_identity_and_settings(
    svc, state_path, monkeypatch
):
    channel = await svc.add_channel("jdoe", "🔵", 15)
    previous_json = state_path.read_text()

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await svc.add_channel("jdoe", "🟢", 20)

    assert svc.get_channel("jdoe") is channel
    assert channel.emoji == "🔵"
    assert channel.interval_minutes == 15
    assert state_path.read_text() == previous_json


@pytest.mark.asyncio
async def test_channel_update_waiting_for_poll_persists_latest_cursor(
    svc, fake_jira, state_path, monkeypatch
):
    channel = await svc.add_channel("jdoe", "🔵", 15)
    old_cursor = datetime(2026, 1, 1, 12, 0)
    new_cursor = datetime(2026, 1, 1, 12, 5)
    channel.last_check = old_cursor
    svc._bot = AsyncMock()
    fake_jira.get_events_since.return_value = []
    monkeypatch.setattr(nots, "utc_now_naive", lambda: new_cursor)

    write_started = threading.Event()
    release_write = threading.Event()
    original_write = svc._write_state
    first_write = True

    def block_poll_write(payload):
        nonlocal first_write
        if first_write:
            first_write = False
            write_started.set()
            assert release_write.wait(timeout=2)
        original_write(payload)

    monkeypatch.setattr(svc, "_write_state", block_poll_write)
    poll_task = asyncio.create_task(svc.check_now("jdoe"))
    assert await asyncio.to_thread(write_started.wait, 1)
    update_task = asyncio.create_task(svc.add_channel("jdoe", "🟢", 20))
    await asyncio.sleep(0)  # update builds its candidate, then waits for poll's save_lock
    release_write.set()

    await poll_task
    updated = await update_task

    assert updated is channel
    assert channel.last_check == new_cursor
    assert channel.interval_minutes == 20
    restored = nots.NotificationService(state_file=state_path).get_channel("jdoe")
    assert restored.last_check == new_cursor
    assert restored.interval_minutes == 20


@pytest.mark.asyncio
async def test_remove_channel(svc):
    await svc.add_channel("jdoe", "🔵", 15)
    assert await svc.remove_channel(100, "jdoe") is True
    assert svc.get_channel("jdoe") is None


@pytest.mark.asyncio
async def test_remove_personal_via_remove_channel_refused(svc):
    svc._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=30)
    assert await svc.remove_channel(100, nots.PERSONAL) is False
    assert nots.PERSONAL in svc._channels


@pytest.mark.asyncio
async def test_removing_last_channel_clears_chat(svc):
    await svc.add_channel("jdoe", "🔵", 15)
    await svc.remove_channel(100, "jdoe")
    assert svc._chat_id is None


@pytest.mark.asyncio
async def test_remove_channel_rejects_another_chat(svc):
    await svc.add_channel("jdoe", "🔵", 15)

    assert await svc.remove_channel(200, "jdoe") is False
    assert svc.get_channel("jdoe") is not None


@pytest.mark.asyncio
async def test_remove_channel_rolls_back_when_state_save_fails(svc, state_path, monkeypatch):
    channel = await svc.add_channel("jdoe", "🔵", 15)

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_state", fail_write)

    with pytest.raises(nots.StateSaveError):
        await svc.remove_channel(100, "jdoe")

    assert svc.get_channel("jdoe") is channel
    assert svc._chat_id == 100
    fresh = nots.NotificationService(state_file=state_path)
    assert fresh.get_channel("jdoe") is not None


@pytest.mark.asyncio
async def test_list_channels_personal_first(svc):
    svc._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=30)
    await svc.add_channel("zoe", "🔵", 15)
    await svc.add_channel("amy", "🟢", 15)
    order = [c.user for c in svc.list_channels()]
    assert order[0] == nots.PERSONAL
    assert order[1:] == ["amy", "zoe"]  # коллеги по имени


@pytest.mark.asyncio
async def test_personal_interval_write_failure_preserves_identity_and_json(
    state_path, fake_jira, monkeypatch
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)
    assert await service.subscribe(100, 15) is True
    channel = service.get_channel(nots.PERSONAL)
    previous_json = state_path.read_text()

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await service.update_interval(100, 30)

    assert service.get_channel(nots.PERSONAL) is channel
    assert channel.interval_minutes == 15
    assert state_path.read_text() == previous_json


@pytest.mark.asyncio
async def test_subscribe_write_failure_does_not_bind_or_publish_channel(
    state_path, fake_jira, monkeypatch
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await service.subscribe(100, 15)

    assert service._chat_id is None
    assert service.get_channel(nots.PERSONAL) is None


@pytest.mark.asyncio
async def test_subscribe_publishes_only_after_disk_write_completes(
    state_path, fake_jira, monkeypatch
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = service._write_state

    def blocked_write(payload):
        write_started.set()
        assert release_write.wait(timeout=2)
        original_write(payload)

    monkeypatch.setattr(service, "_write_state", blocked_write)
    subscribe_task = asyncio.create_task(service.subscribe(100, 15))
    try:
        assert await asyncio.to_thread(write_started.wait, 1)
        assert service._chat_id is None
        assert service.get_channel(nots.PERSONAL) is None
    finally:
        release_write.set()

    assert await subscribe_task is True
    assert service.is_subscribed(100)


@pytest.mark.asyncio
async def test_cancelled_subscribe_finishes_disk_write_before_publishing(
    state_path, fake_jira, monkeypatch
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)
    write_started = threading.Event()
    release_write = threading.Event()
    original_write = service._write_state

    def blocked_write(payload):
        write_started.set()
        assert release_write.wait(timeout=2)
        original_write(payload)

    monkeypatch.setattr(service, "_write_state", blocked_write)
    subscribe_task = asyncio.create_task(service.subscribe(100, 15))
    assert await asyncio.to_thread(write_started.wait, 1)
    subscribe_task.cancel()
    release_write.set()

    with pytest.raises(asyncio.CancelledError):
        await subscribe_task

    assert service.is_subscribed(100)
    restored = nots.NotificationService(state_file=state_path)
    assert restored.is_subscribed(100)


@pytest.mark.asyncio
async def test_mute_and_unmute_write_failures_leave_no_hidden_mutation(
    svc, state_path, monkeypatch
):
    await svc._save_state()
    previous_json = state_path.read_text()

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await svc.mute_user("alice")
    assert not svc.is_user_silent("alice")
    assert state_path.read_text() == previous_json

    monkeypatch.undo()
    await svc.mute_user("alice")
    muted_json = state_path.read_text()
    monkeypatch.setattr(svc, "_write_state", fail_write)
    with pytest.raises(nots.StateSaveError):
        await svc.unmute_user("alice")
    assert svc.is_user_silent("alice")
    assert state_path.read_text() == muted_json


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
    last_text = s._bot.send_message.await_args.kwargs["text"]
    assert "🔵" in last_text
    assert s._bot.send_message.await_args.kwargs["parse_mode"] is None
