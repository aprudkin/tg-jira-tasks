"""Тесты атомарной записи sync_state.json (канальная схема)."""
import json

import bot.services.notifications as nots


def test_save_state_writes_valid_json(state_path):
    svc = nots.NotificationService(state_file=state_path)
    svc._chat_id = 42
    svc._channels[nots.PERSONAL] = nots.Channel(
        user=nots.PERSONAL,
        interval_minutes=15,
        processed_events={"X-1": {"a", "b"}},
    )
    svc._silent_users = {"alice"}

    svc._save_state_sync()

    data = json.loads(state_path.read_text())
    assert data["chat_id"] == 42
    me = data["channels"][nots.PERSONAL]
    assert me["interval_minutes"] == 15
    assert set(me["processed_events"]["X-1"]) == {"a", "b"}
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
