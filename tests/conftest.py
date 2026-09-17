"""Тесты не должны требовать реальные секреты — подкладываем dummy env до импорта bot."""
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("JIRA_URL", "http://jira.test")
os.environ.setdefault("JIRA_PAT", "test-pat")
os.environ.setdefault("ALLOWED_USERS", "1,2,5,42,100,123,200,555,777,999")
os.environ.setdefault("ALLOWED_CHAT_IDS", "-100123")

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def state_path(tmp_path):
    """Изолированный путь к файлу состояния для инъекции в NotificationService.

    Заменяет прежний copy-paste фикстуры, монкипатчившей settings.state_file в 5 файлах.
    """
    return tmp_path / "sync_state.json"


@pytest.fixture
def fake_jira():
    """Фейковый Jira-источник для инъекции: get_events_since — управляемый AsyncMock.

    Заменяет прежний monkeypatch глобала bot.services.notifications.jira_service по строке.
    """
    jira = AsyncMock()
    jira.get_events_since = AsyncMock(return_value=[])
    jira.has_visible_assigned_tasks = AsyncMock(return_value=False)
    return jira
