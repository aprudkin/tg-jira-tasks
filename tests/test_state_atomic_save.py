"""Тесты атомарной записи sync_state.json."""
import json
from pathlib import Path

import pytest

import bot.services.notifications as nots


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Подменяет STATE_FILE на временный путь и возвращает его."""
    state_file = tmp_path / "sync_state.json"
    monkeypatch.setattr(nots, "STATE_FILE", state_file)
    return state_file


def test_save_state_writes_valid_json(isolated_state: Path):
    svc = nots.NotificationService()
    svc._chat_id = 42
    svc._interval_minutes = 15
    svc._processed_events = {"X-1": {"a", "b"}}
    svc._silent_users = {"alice"}

    svc._save_state_sync()

    data = json.loads(isolated_state.read_text())
    assert data["chat_id"] == 42
    assert data["interval_minutes"] == 15
    assert set(data["processed_events"]["X-1"]) == {"a", "b"}
    assert data["silent_users"] == ["alice"]


def test_save_state_does_not_leave_tmp_file(isolated_state: Path):
    svc = nots.NotificationService()
    svc._chat_id = 1
    svc._save_state_sync()

    leftover = list(isolated_state.parent.glob("*.tmp"))
    assert leftover == []


def test_save_state_overwrites_atomically(isolated_state: Path):
    """Второй save полностью заменяет первый, не оставляя мусора."""
    svc = nots.NotificationService()
    svc._chat_id = 1
    svc._save_state_sync()

    svc._chat_id = 2
    svc._save_state_sync()

    data = json.loads(isolated_state.read_text())
    assert data["chat_id"] == 2


def test_save_state_does_not_touch_original_on_serialize_error(isolated_state: Path, monkeypatch):
    """Если сериализация рухнет после первого успешного save — оригинал остаётся."""
    svc = nots.NotificationService()
    svc._chat_id = 1
    svc._save_state_sync()
    original = isolated_state.read_text()

    # Подкладываем не-сериализуемый объект
    svc._processed_events = {"X-1": {object()}}  # set с object() не сериализуется
    svc._save_state_sync()

    # Файл всё ещё содержит первую версию (а не обрезанный/пустой результат)
    assert isolated_state.read_text() == original
