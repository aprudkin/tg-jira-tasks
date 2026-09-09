"""Тесты для форматирования уведомлений."""
from bot.services.jira import JiraEvent
from bot.services.notifications import notification_service


def _plain(content) -> str:
    return content.render()[0]


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
    text = _plain(notification_service._format_event(_event("comment")))
    assert "💬" in text
    assert "Новый комментарий" in text
    assert "X-1" in text
    assert "Alice" in text
    assert "some details" in text


def test_format_event_unknown_type_falls_back_to_generic():
    text = _plain(notification_service._format_event(_event("alien-type")))
    assert "📌" in text
    assert "Обновление" in text


def test_format_event_status_change_shows_arrow():
    event = JiraEvent(
        issue_key="X-2", issue_summary="s", issue_url="u",
        event_type="status_change", author="a", author_id="ai",
        details="In Progress → Done", id="sid",
        to_status="Done",
    )
    text = _plain(notification_service._format_event(event))
    assert "🔄" in text
    assert "Изменение статуса" in text
    assert "→" in text


def test_format_event_marker_precedes_event_icon():
    """Маркер канала коллеги идёт впереди event-type иконки."""
    text = _plain(notification_service._format_event(_event("comment"), "🔵"))
    assert "🔵" in text
    assert text.index("🔵") < text.index("💬")


def test_format_event_no_marker_by_default():
    """Личный канал (marker=None) — без маркера, как раньше."""
    text = _plain(notification_service._format_event(_event("comment")))
    assert "🔵" not in text


def test_format_event_preserves_dynamic_html_as_text():
    event = JiraEvent(
        issue_key="X-3",
        issue_summary="Fix <component> & review",
        issue_url='https://jira.test/browse/X-3?x="quoted"&y=<tag>',
        event_type="comment",
        author="<Alice & Bob>",
        author_id="alice",
        details='<a href="bad">click</a> & done',
        id="evt-html",
    )
    content = notification_service._format_event(event, "<marker>")
    text, entities = content.render()

    assert "<marker>" in text
    assert "Fix <component> & review" in text
    assert "<Alice & Bob>" in text
    assert '<a href="bad">click</a> & done' in text
    assert [entity.type for entity in entities] == ["bold", "text_link"]
