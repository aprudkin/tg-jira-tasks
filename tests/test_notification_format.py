"""Тесты для форматирования уведомлений."""
from bot.services.jira import JiraEvent
from bot.services.notifications import notification_service


def _event(event_type: str = "comment") -> JiraEvent:
    return JiraEvent(
        issue_key="X-1",
        issue_summary="summary",
        issue_url="https://jira.test/browse/X-1",
        event_type=event_type,
        author="Alice",
        author_id="alice",
        details="some details",
        id=f"evt-{event_type}",
    )


def test_format_event_known_type_uses_specific_icon_and_title():
    text = notification_service._format_event(_event("comment"))
    assert "💬" in text
    assert "Новый комментарий" in text
    assert "X-1" in text
    assert "Alice" in text
    assert "some details" in text


def test_format_event_unknown_type_falls_back_to_generic():
    text = notification_service._format_event(_event("alien-type"))
    assert "📌" in text
    assert "Обновление" in text


def test_format_event_status_change_shows_arrow():
    ev = JiraEvent(
        issue_key="X-2", issue_summary="s", issue_url="u",
        event_type="status_change", author="a", author_id="ai",
        details="In Progress → Done", id="sid",
        to_status="Done",
    )
    text = notification_service._format_event(ev)
    assert "🔄" in text
    assert "Изменение статуса" in text
    assert "→" in text
