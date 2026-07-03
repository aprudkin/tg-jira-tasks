"""Интеграция: реальные фоновые циклы каналов + concurrency-safe сохранение.

Гоняем настоящий _channel_loop через start()/stop() (фейки только у Jira/Telegram) —
то, что юнит-тесты с прямым вызовом _check_channel не покрывали. Проверяем, что при
одновременном тике нескольких каналов состояние на диске остаётся валидным и полным
(регресс: общий tmp-файл + межпоточная итерация dict'ов ломали атомарность).
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots
from bot.services.jira import JiraEvent


def _evt(issue_key: str, event_id: str) -> JiraEvent:
    return JiraEvent(
        issue_key=issue_key, issue_summary="s", issue_url=f"u/{issue_key}",
        event_type="comment", author="bob", author_id="bob", details="d", id=event_id,
    )


@pytest.mark.asyncio
async def test_two_channel_loops_tick_deliver_and_persist(state_path):
    async def fake_events(since, target=None):
        if target is None:
            return [_evt("ME-1", "e_me")]
        return [_evt("JD-1", "e_jd")]

    # Первая проверка почти сразу; интервал огромный, чтобы тикнуло ровно по разу
    s = nots.NotificationService(
        jira=SimpleNamespace(get_events_since=fake_events),
        state_file=state_path,
        first_check_delay=0.02,
    )
    s._chat_id = 100
    now = datetime(2026, 1, 1, 12, 0, 0)
    s._channels[nots.PERSONAL] = nots.Channel(user=nots.PERSONAL, interval_minutes=999, last_check=now)
    s._channels["jdoe"] = nots.Channel(user="jdoe", interval_minutes=999, emoji="🔵", last_check=now)

    bot = AsyncMock()
    s.start(bot)
    await asyncio.sleep(0.15)  # даём обоим циклам тикнуть одновременно
    await s.stop()

    # Оба канала доставили своё событие
    assert bot.send_message.await_count == 2

    # Состояние на диске валидно и содержит дедуп ОБОИХ каналов (не потеряно гонкой)
    fresh = nots.NotificationService(state_file=state_path)
    assert fresh._chat_id == 100
    assert fresh.get_channel(nots.PERSONAL).processed_events["ME-1"] == {"e_me"}
    assert fresh.get_channel("jdoe").processed_events["JD-1"] == {"e_jd"}


@pytest.mark.asyncio
async def test_concurrent_saves_produce_valid_state(state_path):
    s = nots.NotificationService(state_file=state_path)
    s._chat_id = 100
    s._channels[nots.PERSONAL] = nots.Channel(
        user=nots.PERSONAL, interval_minutes=30, processed_events={"A-1": {"x"}}
    )
    s._channels["jdoe"] = nots.Channel(
        user="jdoe", interval_minutes=15, emoji="🔵", processed_events={"B-1": {"y"}}
    )

    # 8 конкурентных сохранений не должны побить файл
    await asyncio.gather(*[s._save_state() for _ in range(8)])

    fresh = nots.NotificationService(state_file=state_path)
    assert fresh._chat_id == 100
    assert fresh.get_channel(nots.PERSONAL).processed_events["A-1"] == {"x"}
    assert fresh.get_channel("jdoe").processed_events["B-1"] == {"y"}
