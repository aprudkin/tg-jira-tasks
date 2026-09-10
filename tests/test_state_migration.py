"""Шов №3: загрузка состояния — миграция плоской старой схемы и round-trip новой."""
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots


def test_migrate_flat_state_into_personal_channel(state_path):
    # Старый плоский формат (до многоканальности)
    state_path.write_text(json.dumps({
        "chat_id": 777,
        "interval_minutes": 15,
        "processed_events": {"ABC-1": ["c1", "c2"]},
        "silent_users": ["alice"],
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc._chat_id == 777
    me = svc.get_channel(nots.PERSONAL)
    assert me is not None
    assert me.interval_minutes == 15
    assert me.emoji is None
    # Дедуп сохранён при миграции
    assert me.processed_events["ABC-1"] == {"c1", "c2"}
    assert svc._silent_users == {"alice"}
    # Только личный канал, коллег в старом формате не было
    assert list(svc._channels.keys()) == [nots.PERSONAL]


def test_new_schema_roundtrip(state_path):
    state_path.write_text(json.dumps({
        "chat_id": 100,
        "schema_version": 2,
        "channels": {
            nots.PERSONAL: {"interval_minutes": 30, "emoji": None, "cursor_utc": "2026-01-01T12:00:00Z", "processed_events": {}},
            "jdoe": {"interval_minutes": 15, "emoji": "🔵", "cursor_utc": "2026-01-01T12:01:00Z", "processed_events": {"X-1": {"c1": "2026-01-01T12:00:30Z"}}},
        },
        "silent_users": [],
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc._chat_id == 100
    assert svc.get_channel("jdoe").emoji == "🔵"
    assert svc.get_channel("jdoe").interval_minutes == 15
    assert svc.get_channel("jdoe").processed_events["X-1"] == {"c1"}
    assert svc.get_channel(nots.PERSONAL).emoji is None
    assert svc.get_channel("jdoe").last_check == datetime(2026, 1, 1, 12, 1)


def test_legacy_multichannel_state_without_cursor_uses_current_baseline(state_path, monkeypatch):
    baseline = datetime(2026, 1, 1, 12, 0)
    monkeypatch.setattr(nots, "utc_now_naive", lambda: baseline)
    state_path.write_text(json.dumps({
        "chat_id": 100,
        "channels": {"jdoe": {"interval_minutes": 15, "emoji": "🔵", "processed_events": {"X-1": ["c1"]}}},
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc.get_channel("jdoe").last_check == baseline
    assert svc.get_channel("jdoe").processed_events["X-1"] == {"c1"}


@pytest.mark.asyncio
async def test_legacy_baseline_is_persisted_before_first_poll(state_path, fake_jira):
    state_path.write_text(json.dumps({
        "chat_id": 100,
        "channels": {"jdoe": {"interval_minutes": 15, "emoji": "🔵", "processed_events": {}}},
    }))
    svc = nots.NotificationService(jira=fake_jira, state_file=state_path)
    svc._bot = AsyncMock()

    await svc.check_now("jdoe")

    saved = json.loads(state_path.read_text())
    assert saved["schema_version"] == 2
    assert saved["channels"]["jdoe"]["cursor_utc"].endswith("Z")
    fake_jira.get_events_since.assert_awaited_once()


def test_old_list_processed_ids_dropped(state_path):
    """Совсем старый формат (список ID без привязки к задачам) — дедуп сбрасывается."""
    state_path.write_text(json.dumps({
        "chat_id": 5,
        "interval_minutes": 30,
        "processed_ids": ["c1", "c2"],
    }))

    svc = nots.NotificationService(state_file=state_path)

    me = svc.get_channel(nots.PERSONAL)
    assert me is not None
    assert me.processed_events == {}


def test_no_state_file_starts_empty(state_path):
    # Файла нет — сервис стартует без каналов и подписки
    svc = nots.NotificationService(state_file=state_path)
    assert svc._chat_id is None
    assert svc._channels == {}


def test_malformed_persisted_cursor_disables_polling_state(state_path):
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "chat_id": 100,
        "channels": {
            "jdoe": {
                "interval_minutes": 15,
                "emoji": "🔵",
                "cursor_utc": "not-a-date",
                "processed_events": {},
            }
        },
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc._chat_id is None
    assert svc._channels == {}


def test_current_schema_without_cursor_disables_polling_state(state_path):
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "chat_id": 100,
        "channels": {
            "jdoe": {
                "interval_minutes": 15,
                "emoji": "🔵",
                "processed_events": {},
            }
        },
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc._chat_id is None
    assert svc._channels == {}


def test_future_cursor_is_clamped_and_persisted(state_path, monkeypatch):
    now = datetime(2026, 1, 1, 12, 0)
    monkeypatch.setattr(nots, "utc_now_naive", lambda: now)
    state_path.write_text(json.dumps({
        "schema_version": 2,
        "chat_id": 100,
        "channels": {
            "jdoe": {
                "interval_minutes": 15,
                "emoji": "🔵",
                "cursor_utc": "2026-01-01T13:00:00Z",
                "processed_events": {},
            }
        },
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc.get_channel("jdoe").last_check == now
    saved = json.loads(state_path.read_text())
    assert saved["channels"]["jdoe"]["cursor_utc"] == "2026-01-01T12:00:00Z"
