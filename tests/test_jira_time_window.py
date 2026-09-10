"""Регрессии временного окна событий Jira (#5)."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import bot.services.jira as jira_module
from bot.services.jira import JiraService


def _service_with_comments(comments):
    issue = SimpleNamespace(
        key="ABC-1",
        fields=SimpleNamespace(
            summary="Summary",
            status=SimpleNamespace(name="In Progress"),
            created="2026-01-01T11:00:00.000+0000",
            reporter=SimpleNamespace(name="rep", displayName="Reporter", accountId="rep"),
            assignee=SimpleNamespace(displayName="Assignee"),
            comment=SimpleNamespace(comments=comments, total=len(comments), startAt=0),
        ),
        changelog=SimpleNamespace(histories=[], total=0, startAt=0),
    )
    client = MagicMock()
    client._is_cloud = False
    client.current_user.return_value = "me"
    client.search_issues.return_value = [issue]
    service = JiraService()
    service._client = client
    return service, client


def _comment(comment_id: str, created: str):
    return SimpleNamespace(
        id=comment_id,
        created=created,
        body=comment_id,
        author=SimpleNamespace(name="author", displayName="Author", accountId="author"),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-01T12:00:00.800+0000", datetime(2026, 1, 1, 12, 0, 0, 800000)),
        ("2026-01-01T15:00:00.800+0300", datetime(2026, 1, 1, 12, 0, 0, 800000)),
        ("2026-01-01T07:00:00.800-0500", datetime(2026, 1, 1, 12, 0, 0, 800000)),
        ("2026-03-08T01:59:59.800-0500", datetime(2026, 3, 8, 6, 59, 59, 800000)),
        ("2026-03-08T03:00:00.800-0400", datetime(2026, 3, 8, 7, 0, 0, 800000)),
    ],
)
def test_parse_jira_datetime_preserves_instant_and_fraction(value, expected):
    assert JiraService._parse_jira_datetime(value) == expected


def test_parse_jira_datetime_rejects_timestamp_without_timezone():
    assert JiraService._parse_jira_datetime("2026-01-01T12:00:00.800") is None


def test_relative_jql_window_rounds_up_and_adds_clock_skew_overlap():
    since = datetime(2026, 1, 1, 12, 0, 0, 500000)
    query_now = datetime(2026, 1, 1, 12, 1, 1)

    assert JiraService._relative_jql_lookback(since, query_now) == "-4m"


def test_event_window_uses_relative_jql_and_exact_utc_boundaries(monkeypatch):
    since = datetime(2026, 1, 1, 12, 0, 0, 500000)
    until = datetime(2026, 1, 1, 12, 1, 0, 500000)
    comments = [
        _comment("at-cursor", "2026-01-01T12:00:00.500+0000"),
        _comment("after-cursor", "2026-01-01T15:00:00.800+0300"),
        _comment("at-end", "2026-01-01T07:01:00.500-0500"),
        _comment("after-end", "2026-01-01T12:01:00.501+0000"),
    ]
    service, client = _service_with_comments(comments)
    monkeypatch.setattr(
        jira_module,
        "utc_now_naive",
        lambda: datetime(2026, 1, 1, 12, 1, 1),
    )

    events = service._get_events_since_sync(since, until=until)

    assert [event.id for event in events] == ["comment_after-cursor", "comment_at-end"]
    jql = client.search_issues.call_args.args[0]
    assert 'updated >= "-4m"' in jql
    assert "2026-01-01" not in jql
