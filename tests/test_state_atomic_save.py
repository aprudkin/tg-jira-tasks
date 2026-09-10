"""Тесты атомарной записи sync_state.json (канальная схема)."""
import json
from datetime import datetime

import bot.services.notifications as nots


def test_save_state_writes_valid_json(state_path):
    svc = nots.NotificationService(state_file=state_path)
    svc._chat_id = 42
    svc._channels[nots.PERSONAL] = nots.Channel(
        user=nots.PERSONAL,
        interval_minutes=15,
        processed_events={"X-1": {"a", "b"}},
        processed_event_times={"X-1": {"a": datetime(2026, 1, 1, 12), "b": datetime(2026, 1, 1, 12, 1)}},
        last_check=datetime(2026, 1, 1, 12, 2),
    )
    svc._silent_users = {"alice"}

    svc._save_state_sync()

    data = json.loads(state_path.read_text())
    assert data["chat_id"] == 42
    me = data["channels"][nots.PERSONAL]
    assert me["interval_minutes"] == 15
    assert set(me["processed_events"]["X-1"]) == {"a", "b"}
    assert data["schema_version"] == 2
    assert me["cursor_utc"] == "2026-01-01T12:02:00Z"
    restored = nots.NotificationService(state_file=state_path)
    assert restored.get_channel(nots.PERSONAL).last_check == datetime(2026, 1, 1, 12, 2)
    assert data["silent_users"] == ["alice"]


def test_save_state_does_not_leave_tmp_file(state_path):
    svc = nots.NotificationService(state_file=state_path)
    svc._chat_id = 1
    svc._save_state_sync()

    leftover = list(state_path.parent.glob("*.tmp"))
    assert leftover == []


def test_save_state_overwrites_atomically(state_path):
    """Второй save полностью заменяет первый, не оставляя мусора."""
    svc = nots.NotificationService(state_file=state_path)
    svc._chat_id = 1
    svc._save_state_sync()

    svc._chat_id = 2
    svc._save_state_sync()

    data = json.loads(state_path.read_text())
    assert data["chat_id"] == 2


def test_save_state_does_not_touch_original_on_serialize_error(state_path):
    """Если сериализация рухнет после первого успешного save — оригинал остаётся."""
    svc = nots.NotificationService(state_file=state_path)
    svc._chat_id = 1
    svc._save_state_sync()
    original = state_path.read_text()

    # Подкладываем не-сериализуемый объект в дедуп канала
    svc._channels[nots.PERSONAL] = nots.Channel(
        user=nots.PERSONAL,
        interval_minutes=30,
        processed_events={"X-1": {object()}},  # set с object() не сериализуется
    )
    svc._save_state_sync()

    # Файл всё ещё содержит первую версию (а не обрезанный/пустой результат)
    assert state_path.read_text() == original
