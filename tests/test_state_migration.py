"""Шов №3: загрузка состояния — миграция плоской старой схемы и round-trip новой."""
import json

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
        "channels": {
            nots.PERSONAL: {"interval_minutes": 30, "emoji": None, "processed_events": {}},
            "jdoe": {"interval_minutes": 15, "emoji": "🔵", "processed_events": {"X-1": ["c1"]}},
        },
        "silent_users": [],
    }))

    svc = nots.NotificationService(state_file=state_path)

    assert svc._chat_id == 100
    assert svc.get_channel("jdoe").emoji == "🔵"
    assert svc.get_channel("jdoe").interval_minutes == 15
    assert svc.get_channel("jdoe").processed_events["X-1"] == {"c1"}
    assert svc.get_channel(nots.PERSONAL).emoji is None


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
