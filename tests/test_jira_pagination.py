"""Контрактные тесты пагинации Jira Cloud/Data Center без сетевых запросов."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.services.notifications as nots
from bot.services.jira import IncompleteJiraDataError, JiraService

SINCE = datetime(2026, 1, 1, 12, 0, 0)
AFTER = "2026-01-02T10:00:00.000+0000"
BEFORE = "2026-01-01T09:00:00.000+0000"


class Page(list):
    """Минимальная модель ResultList с pagination metadata."""

    def __init__(
        self,
        values,
        *,
        start_at=0,
        max_results=100,
        total=None,
        is_last=False,
        next_token=None,
    ):
        super().__init__(values)
        self.startAt = start_at
        self.maxResults = max_results
        self.total = len(values) if total is None else total
        self.isLast = is_last
        self.nextPageToken = next_token


def _task_issue(index: int):
    return SimpleNamespace(
        key=f"ABC-{index:04d}",
        fields=SimpleNamespace(
            summary=f"Task {index}",
            status=SimpleNamespace(name="In Progress"),
            assignee=None,
        ),
    )


def _event_issue(*, comments, comment_total, histories, history_total, raw=True):
    fields = SimpleNamespace(
        summary="Summary",
        status=SimpleNamespace(name="In Progress"),
        created=BEFORE,
        reporter=SimpleNamespace(name="reporter", displayName="Reporter", accountId="reporter"),
        assignee=SimpleNamespace(displayName="Assignee"),
        comment=SimpleNamespace(comments=comments, startAt=0, total=comment_total),
    )
    issue = SimpleNamespace(
        key="ABC-1",
        fields=fields,
        changelog=SimpleNamespace(histories=histories, startAt=0, total=history_total),
    )
    if raw:
        issue.raw = {}
    return issue


def _comment(comment_id: str):
    return SimpleNamespace(
        id=comment_id,
        created=AFTER,
        body=f"Comment {comment_id}",
        author=SimpleNamespace(name="author", accountId="author", displayName="Author"),
    )


def _history(history_id: str):
    return SimpleNamespace(
        id=history_id,
        created=AFTER,
        author=SimpleNamespace(name="author", accountId="author", displayName="Author"),
        items=[SimpleNamespace(
            field="status",
            **{"from": "1", "to": "2"},
            fromString="To Do",
            toString="In Progress",
        )],
    )


def _service(client) -> JiraService:
    service = JiraService()
    service._client = client
    return service


def test_data_center_offset_pages_return_several_hundred_tasks():
    all_issues = [_task_issue(index) for index in range(350)]
    client = MagicMock()
    client._is_cloud = False

    def search(jql, *, startAt, maxResults, **kwargs):
        values = all_issues[startAt:startAt + maxResults]
        return Page(values, start_at=startAt, max_results=maxResults, total=len(all_issues))

    client.search_issues.side_effect = search

    tasks = _service(client)._search_issues("assignee = currentUser()")

    assert len(tasks) == 350
    assert [call.kwargs["startAt"] for call in client.search_issues.call_args_list] == [0, 100, 200, 300]
    assert client.search_issues.call_args.args[0].endswith("ORDER BY key ASC")


def test_cloud_token_pages_deduplicate_repeated_boundary_issue():
    first = [_task_issue(index) for index in range(100)]
    second = [_task_issue(99), _task_issue(100)]
    client = MagicMock()
    client._is_cloud = True

    def search(jql, *, nextPageToken, **kwargs):
        if nextPageToken is None:
            return Page(first, next_token="page-2")
        assert nextPageToken == "page-2"
        return Page(second, next_token=None, is_last=True)

    client.enhanced_search_issues.side_effect = search

    tasks = _service(client)._search_issues("assignee = currentUser()")

    assert len(tasks) == 101
    assert len({task.key for task in tasks}) == 101


def test_repeated_cloud_page_token_fails_instead_of_looping():
    client = MagicMock()
    client._is_cloud = True
    client.enhanced_search_issues.side_effect = [
        Page([_task_issue(1)], next_token="same"),
        Page([_task_issue(2)], next_token="same"),
    ]

    with pytest.raises(IncompleteJiraDataError, match="repeated page token"):
        _service(client)._search_issues("assignee = currentUser()")


def test_empty_data_center_page_before_total_fails_closed():
    client = MagicMock()
    client._is_cloud = False
    client.search_issues.return_value = Page([], start_at=0, total=101)

    with pytest.raises(IncompleteJiraDataError, match="empty page before total"):
        _service(client)._search_issue_pages(
            "assignee = currentUser()",
            fields=["key"],
            limit=500,
        )


def test_empty_cloud_page_with_continuation_fails_closed():
    client = MagicMock()
    client._is_cloud = True
    client.enhanced_search_issues.return_value = Page([], next_token="another-page")

    with pytest.raises(IncompleteJiraDataError, match="did not add any issues"):
        _service(client)._search_issue_pages(
            "assignee = currentUser()",
            fields=["key"],
            limit=500,
        )


def test_interactive_limit_fails_without_returning_partial_tasks():
    client = MagicMock()
    client._is_cloud = False
    issues = [_task_issue(index) for index in range(101)]

    def search(jql, *, startAt, maxResults, **kwargs):
        values = issues[startAt:startAt + maxResults]
        return Page(values, start_at=startAt, max_results=maxResults, total=len(issues))

    client.search_issues.side_effect = search
    service = _service(client)
    service.INTERACTIVE_RESULT_LIMIT = 100

    with pytest.raises(IncompleteJiraDataError, match="exceeded 100"):
        service._search_issues("assignee = currentUser()")


@pytest.mark.asyncio
async def test_second_search_page_error_does_not_advance_channel_cursor(tmp_path):
    client = MagicMock()
    client._is_cloud = False
    client.current_user.return_value = "me"
    client.search_issues.side_effect = [
        Page([_event_issue(comments=[], comment_total=0, histories=[], history_total=0)] * 100,
             start_at=0, max_results=100, total=101),
        RuntimeError("second page failed"),
    ]
    jira = _service(client)
    service = nots.NotificationService(jira=jira, state_file=tmp_path / "state.json")
    service._bot = AsyncMock()
    service._chat_id = 1
    channel = nots.Channel(user=nots.PERSONAL, interval_minutes=30, last_check=SINCE)
    service._channels[nots.PERSONAL] = channel

    await service.check_now()

    assert channel.last_check == SINCE
    service._bot.send_message.assert_not_awaited()


def test_cloud_truncated_comments_and_changelog_continue_from_page_zero():
    issue = _event_issue(
        comments=[_comment("embedded")],
        comment_total=2,
        histories=[_history("embedded")],
        history_total=2,
    )
    client = MagicMock()
    client._is_cloud = True
    client._options = {}
    client._session = None
    client.current_user.return_value = "me"
    client.enhanced_search_issues.return_value = Page([issue], is_last=True)

    comment_rows = [
        {"id": "c1", "created": AFTER, "body": "One", "author": {"name": "a", "displayName": "A"}},
        {"id": "c2", "created": AFTER, "body": "Two", "author": {"name": "a", "displayName": "A"}},
    ]
    history_rows = [
        {"id": "h1", "created": AFTER, "author": {"name": "a", "displayName": "A"},
         "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
        {"id": "h2", "created": AFTER, "author": {"name": "a", "displayName": "A"},
         "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
    ]

    def history_page(path, params):
        start_at = params["startAt"]
        key = "comments" if path.endswith("comment") else "values"
        rows = comment_rows if key == "comments" else history_rows
        return {
            "startAt": start_at,
            "maxResults": 1,
            "total": 2,
            key: rows[start_at:start_at + 1],
        }

    client._get_json.side_effect = history_page
    service = _service(client)
    service.PAGE_SIZE = 1

    events = service._get_events_since_sync(SINCE)

    assert {event.id for event in events} == {"comment_c1", "comment_c2", "status_h1_status", "status_h2_status"}
    assert [(call.args[0], call.kwargs["params"]["startAt"]) for call in client._get_json.call_args_list] == [
        ("issue/ABC-1/comment", 0),
        ("issue/ABC-1/comment", 1),
        ("issue/ABC-1/changelog", 0),
        ("issue/ABC-1/changelog", 1),
    ]


def test_data_center_refetches_truncated_comments_page_by_page():
    issue = _event_issue(comments=[_comment("c1")], comment_total=2, histories=[], history_total=0)
    client = MagicMock()
    client._is_cloud = False
    client._options = {}
    client._session = None
    client.current_user.return_value = "me"
    client.search_issues.return_value = Page([issue], is_last=True)
    rows = [
        {"id": "c1", "created": AFTER, "body": "One", "author": {"name": "a", "displayName": "A"}},
        {"id": "c2", "created": AFTER, "body": "Two", "author": {"name": "a", "displayName": "A"}},
    ]

    def comments_page(path, params):
        start_at = params["startAt"]
        return {
            "startAt": start_at,
            "maxResults": 1,
            "total": 2,
            "comments": rows[start_at:start_at + 1],
        }

    client._get_json.side_effect = comments_page
    service = _service(client)
    service.PAGE_SIZE = 1

    events = service._get_events_since_sync(SINCE)

    assert {event.id for event in events} == {"comment_c1", "comment_c2"}
    assert [call.kwargs["params"]["startAt"] for call in client._get_json.call_args_list] == [0, 1]


def test_empty_history_page_before_total_fails_closed():
    client = MagicMock()
    client._is_cloud = True
    client._options = {}
    client._session = None
    client._get_json.return_value = {
        "startAt": 0,
        "maxResults": 100,
        "total": 1,
        "comments": [],
    }

    with pytest.raises(IncompleteJiraDataError, match="Empty comment page before total"):
        _service(client)._fetch_offset_history("ABC-1", "comment", "comments")


def test_complete_embedded_history_still_obeys_per_issue_limit():
    issue = _event_issue(comments=[], comment_total=0, histories=[_history("h1"), _history("h2")], history_total=2)
    client = MagicMock()
    client._is_cloud = False
    service = _service(client)
    service.HISTORY_LIMIT_PER_ISSUE = 1

    with pytest.raises(IncompleteJiraDataError, match="Changelog for ABC-1 exceeded 1"):
        service._complete_changelog(issue)


def test_data_center_truncated_changelog_fails_closed():
    issue = _event_issue(comments=[], comment_total=0, histories=[_history("h1")], history_total=2)
    client = MagicMock()
    client._is_cloud = False
    client.current_user.return_value = "me"
    client.search_issues.return_value = Page([issue], is_last=True)

    with pytest.raises(IncompleteJiraDataError, match="no supported continuation"):
        _service(client)._get_events_since_sync(SINCE)
