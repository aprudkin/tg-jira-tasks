"""Regression tests for identities embedded in JQL string literals."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from bot.services.jira import JiraService, _jql_identity_literal


def _service_with_client(*, cloud: bool = False) -> tuple[JiraService, MagicMock]:
    service = JiraService()
    client = MagicMock()
    client._is_cloud = cloud
    service._client = client
    return service, client


def test_jql_identity_literal_escapes_quotes_and_backslashes():
    assert _jql_identity_literal('user"name\\account') == '"user\\"name\\\\account"'


def test_jql_identity_literal_preserves_valid_jira_identity_characters():
    identity = "user name:+/=@._-кириллица"
    assert _jql_identity_literal(identity) == f'"{identity}"'


@pytest.mark.parametrize("control", ["\x00", "\t", "\n", "\r", "\x7f", "\x85"])
def test_jql_identity_literal_rejects_control_characters_without_echoing_input(control):
    identity = f"private{control}identity"

    with pytest.raises(ValueError) as exc_info:
        _jql_identity_literal(identity)

    assert identity not in str(exc_info.value)
    assert "control characters" in str(exc_info.value)


def test_visibility_probe_uses_escaped_identity_literal():
    service, client = _service_with_client()
    client.search_issues.return_value = {"issues": []}

    service._has_visible_assigned_tasks_sync('user"name\\account')

    assert client.search_issues.call_args.args[0] == 'assignee = "user\\"name\\\\account"'


def test_colleague_event_query_uses_escaped_identity_literal(monkeypatch):
    service, _ = _service_with_client()
    captured: dict[str, str] = {}

    def fake_search(jql, **kwargs):
        captured["jql"] = jql
        return []

    monkeypatch.setattr(service, "_search_issue_pages", fake_search)
    service._get_events_since_sync(
        datetime(2026, 1, 1, 12, 0),
        target='user"name\\account',
        until=datetime(2026, 1, 1, 12, 1),
    )

    assert 'assignee = "user\\"name\\\\account"' in captured["jql"]
