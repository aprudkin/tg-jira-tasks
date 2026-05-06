"""Тесты для чистых утилит из bot.services.jira."""
from datetime import datetime, timezone

from bot.services.jira import utc_now_naive, JiraEvent


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
