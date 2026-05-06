"""Тесты для чистых утилит из bot.services.jira."""
from datetime import datetime, timezone

from bot.services.jira import utc_now_naive, JiraEvent, CLOSED_STATUSES


def test_utc_now_naive_returns_naive_utc():
    """utc_now_naive должен возвращать naive datetime, близкий к UTC now."""
    t = utc_now_naive()
    assert t.tzinfo is None
    aware_now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = abs((aware_now - t).total_seconds())
    assert delta < 2.0, f"slack too large: {delta}s"


def test_utc_now_naive_independent_of_local_tz(monkeypatch):
    """На не-UTC хосте utc_now_naive должен оставаться UTC, а не локальным временем."""
    import time
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        local_naive = datetime.now()
        utc_naive = utc_now_naive()
        diff_hours = (utc_naive - local_naive).total_seconds() / 3600
        # EST (-5) или EDT (-4) → UTC опережает на ~4-5 часов
        assert 3.5 < diff_hours < 5.5, f"unexpected diff: {diff_hours}h"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_closed_statuses_membership():
    """Сanity check, чтобы случайно не сузили/расширили множество."""
    assert "Done" in CLOSED_STATUSES
    assert "Closed" in CLOSED_STATUSES
    assert "Resolved" in CLOSED_STATUSES
    # Reopen — НЕ закрытый, иначе дедуп ломается
    assert "Reopened" not in CLOSED_STATUSES
    assert "In Progress" not in CLOSED_STATUSES


def test_status_change_to_status_carried_explicitly():
    """to_status должен быть только у status_change событий и нести toString из Jira."""
    ev = JiraEvent(
        issue_key="X-1", issue_summary="s", issue_url="u",
        event_type="status_change", author="a", author_id="ai",
        details="Resolved → Reopened", id="sid",
        to_status="Reopened",
    )
    assert ev.to_status == "Reopened"
    # Раньше substring-проверка по details ловила "Resolved" — теперь нет.
    assert ev.to_status not in CLOSED_STATUSES


def test_jira_event_default_timestamp_is_utc_naive():
    """JiraEvent.timestamp по умолчанию — naive UTC."""
    ev = JiraEvent(
        issue_key="X-1",
        issue_summary="s",
        issue_url="u",
        event_type="comment",
        author="a",
        author_id="ai",
        details="d",
        id="cid",
    )
    assert ev.timestamp.tzinfo is None
    aware_now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((aware_now - ev.timestamp).total_seconds()) < 2.0
