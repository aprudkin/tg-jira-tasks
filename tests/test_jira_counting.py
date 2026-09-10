"""Контрактные тесты ограниченного подсчёта и пробы Jira Cloud/Data Center."""

from unittest.mock import MagicMock

import pytest

from bot.services.jira import IncompleteJiraDataError, JiraService, JiraStats


def _service(client) -> JiraService:
    service = JiraService()
    service._client = client
    return service


def _raw_issue(index: int) -> dict[str, str]:
    return {"id": str(index), "key": f"ABC-{index:05d}"}


@pytest.mark.parametrize(
    ("total", "issues"),
    [
        (0, []),
        (1, [_raw_issue(1)]),
        (1001, [_raw_issue(1)]),
    ],
)
def test_data_center_count_uses_single_minimal_request(total, issues):
    client = MagicMock()
    client._is_cloud = False
    client.search_issues.return_value = {"issues": issues, "total": total}

    assert _service(client)._count_issues("project = ABC") == total

    client.search_issues.assert_called_once_with(
        "project = ABC",
        maxResults=1,
        fields=[],
        json_result=True,
    )


@pytest.mark.parametrize("total", [0, 1, 1001])
def test_cloud_count_reads_every_minimal_token_page_without_total(total):
    all_issues = [_raw_issue(index) for index in range(total)]
    client = MagicMock()
    client._is_cloud = True

    def search(jql, *, nextPageToken, maxResults, fields, json_result):
        start = int(nextPageToken) if nextPageToken is not None else 0
        issues = all_issues[start:start + maxResults]
        next_start = start + len(issues)
        response = {"issues": issues, "isLast": next_start >= total}
        if next_start < total:
            response["nextPageToken"] = str(next_start)
        return response

    client.enhanced_search_issues.side_effect = search

    assert _service(client)._count_issues("project = ABC") == total
    assert client.enhanced_search_issues.call_count == max(1, (total + 99) // 100)
    for call in client.enhanced_search_issues.call_args_list:
        assert call.args[0].endswith("ORDER BY key ASC")
        assert call.kwargs["maxResults"] == 100
        assert call.kwargs["fields"] == []
        assert call.kwargs["json_result"] is True


def test_cloud_count_rejects_full_page_without_terminal_metadata():
    client = MagicMock()
    client._is_cloud = True
    client.enhanced_search_issues.return_value = {
        "issues": [_raw_issue(index) for index in range(100)],
    }

    with pytest.raises(IncompleteJiraDataError, match="last-page marker"):
        _service(client)._count_issues("project = ABC")


def test_cloud_count_rejects_duplicate_issue_instead_of_returning_wrong_total():
    client = MagicMock()
    client._is_cloud = True
    client.enhanced_search_issues.side_effect = [
        {"issues": [_raw_issue(1)], "nextPageToken": "page-2", "isLast": False},
        {"issues": [_raw_issue(1)], "isLast": True},
    ]

    with pytest.raises(IncompleteJiraDataError, match="duplicate issue"):
        _service(client)._count_issues("project = ABC")


def test_cloud_count_fails_when_page_limit_would_truncate_result():
    client = MagicMock()
    client._is_cloud = True
    client.enhanced_search_issues.side_effect = [
        {"issues": [_raw_issue(1)], "nextPageToken": "page-2", "isLast": False},
        {"issues": [_raw_issue(2)], "nextPageToken": "page-3", "isLast": False},
    ]
    service = _service(client)
    service.COUNT_PAGE_LIMIT = 2

    with pytest.raises(IncompleteJiraDataError, match="page request limit"):
        service._count_issues("project = ABC")


def test_data_center_count_rejects_missing_or_invalid_total():
    client = MagicMock()
    client._is_cloud = False
    service = _service(client)

    for total in (None, True, -1):
        client.search_issues.return_value = {"issues": [], "total": total}
        with pytest.raises(IncompleteJiraDataError, match="invalid total"):
            service._count_issues("project = ABC")


@pytest.mark.parametrize(("is_cloud", "method_name"), [(False, "search_issues"), (True, "enhanced_search_issues")])
def test_visibility_probe_uses_one_minimal_request(is_cloud, method_name):
    client = MagicMock()
    client._is_cloud = is_cloud
    method = getattr(client, method_name)
    method.return_value = {"issues": [_raw_issue(1)], "nextPageToken": "ignored"}

    assert _service(client)._has_visible_assigned_tasks_sync("jdoe") is True

    method.assert_called_once_with(
        'assignee = "jdoe"',
        maxResults=1,
        fields=[],
        json_result=True,
    )


@pytest.mark.parametrize(("is_cloud", "method_name"), [(False, "search_issues"), (True, "enhanced_search_issues")])
def test_count_and_probe_propagate_rate_limit_without_partial_result(is_cloud, method_name):
    client = MagicMock()
    client._is_cloud = is_cloud
    method = getattr(client, method_name)
    method.side_effect = RuntimeError("429 rate limit")
    service = _service(client)

    with pytest.raises(RuntimeError, match="429"):
        service._count_issues("project = ABC")
    assert method.call_count == 1

    method.reset_mock(side_effect=True)
    method.side_effect = RuntimeError("429 rate limit")
    with pytest.raises(RuntimeError, match="429"):
        service._has_visible_assigned_tasks_sync("jdoe")
    assert method.call_count == 1


@pytest.mark.asyncio
async def test_get_stats_preserves_each_exact_counter(monkeypatch):
    service = JiraService()
    counts = iter((1, 1001, 0, 42))
    monkeypatch.setattr(service, "_count_issues", lambda jql: next(counts))

    assert await service.get_stats() == JiraStats(1, 1001, 0, 42)
