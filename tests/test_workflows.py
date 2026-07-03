"""Оркестрация /sync и /track, вынесенная в NotificationService (кандидат D).

Раньше это была логика в необтестированных хендлерах; теперь — методы сервиса,
принимающие решение и меняющие состояние (без check_now — тот остаётся за хендлером).
"""
import pytest

import bot.services.notifications as nots


@pytest.fixture
def svc(state_path, fake_jira):
    # Без _bot: subscribe/add_channel не поднимают фоновые задачи (_start_channel_task no-op)
    return nots.NotificationService(jira=fake_jira, state_file=state_path)


# ---- enable_personal ------------------------------------------------------

@pytest.mark.asyncio
async def test_enable_personal_new_subscription(svc):
    outcome = await svc.enable_personal(100, 15)
    assert outcome.status == "enabled"
    assert outcome.interval == 15
    assert svc.is_subscribed(100)


@pytest.mark.asyncio
async def test_enable_personal_default_interval_when_none(svc):
    outcome = await svc.enable_personal(100, None)
    assert outcome.status == "enabled"
    assert outcome.interval == svc.DEFAULT_INTERVAL_MINUTES


@pytest.mark.asyncio
async def test_enable_personal_interval_changed(svc):
    await svc.enable_personal(100, 15)
    outcome = await svc.enable_personal(100, 30)
    assert outcome.status == "interval_changed"
    assert outcome.old_interval == 15
    assert outcome.interval == 30
    assert svc.get_interval() == 30


@pytest.mark.asyncio
async def test_enable_personal_unchanged(svc):
    await svc.enable_personal(100, 15)
    outcome = await svc.enable_personal(100, 15)
    assert outcome.status == "unchanged"
    assert outcome.interval == 15


@pytest.mark.asyncio
async def test_enable_personal_chat_busy(svc):
    await svc.enable_personal(100, 15)  # чат привязан к 100
    outcome = await svc.enable_personal(999, 15)  # другой чат
    assert outcome.status == "chat_busy"


# ---- track_colleague ------------------------------------------------------

@pytest.mark.asyncio
async def test_track_colleague_success(svc, fake_jira):
    fake_jira.count_assigned.return_value = 3
    outcome = await svc.track_colleague(100, "jdoe", "🔵", 20)
    assert outcome.status == "tracked"
    assert outcome.assigned_count == 3
    assert outcome.channel.user == "jdoe"
    assert outcome.channel.emoji == "🔵"
    assert svc.get_channel("jdoe") is not None


@pytest.mark.asyncio
async def test_track_colleague_probe_failure_adds_no_channel(svc, fake_jira):
    fake_jira.count_assigned.side_effect = RuntimeError("no perms")
    outcome = await svc.track_colleague(100, "jdoe")
    assert outcome.status == "probe_failed"
    assert svc.get_channel("jdoe") is None


@pytest.mark.asyncio
async def test_track_colleague_chat_busy(svc):
    await svc.enable_personal(100, 15)  # чат уже привязан к 100
    outcome = await svc.track_colleague(999, "jdoe")
    assert outcome.status == "chat_busy"
    assert svc.get_channel("jdoe") is None
