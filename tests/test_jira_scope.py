"""Шов №4: get_events_since(since, target) — область поиска по целевому юзеру.

Мокаем JIRA-клиент (внешняя зависимость), наблюдаем переданный JQL и собранные события.
Проверяем контракт по полям, а не точную строку JQL:
- личный канал (target=None) → assignee + reporter + watcher по currentUser();
- канал коллеги (target="jdoe") → assignee-only, без reporter/watcher;
- событие assigned срабатывает только когда item.to == target.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from bot.services.jira import jira_service

SINCE = datetime(2026, 1, 1, 12, 0, 0)
# Дата в формате Jira, ПОЗЖЕ since — чтобы changelog-запись прошла фильтр
AFTER = "2026-01-02T10:00:00.000+0000"
# Дата создания РАНЬШЕ since — чтобы не порождать событие "created"
BEFORE = "2026-01-01T09:00:00.000+0000"


def _issue(key="ABC-1", assignee_to=None):
    """Фейковый issue с одной changelog-записью смены assignee (если задано)."""
    fields = SimpleNamespace(
        summary="sum",
        status=SimpleNamespace(name="In Progress"),
        created=BEFORE,
        reporter=SimpleNamespace(name="rep", displayName="Rep", accountId="rep"),
        assignee=SimpleNamespace(displayName="Asg"),
        comment=SimpleNamespace(comments=[]),
    )
    histories = []
    if assignee_to is not None:
        item = SimpleNamespace(field="assignee", to=assignee_to, toString="Disp", fromString=None)
        histories.append(SimpleNamespace(
            id="h1",
            created=AFTER,
            author=SimpleNamespace(name="act", displayName="Act", accountId="act"),
            items=[item],
        ))
    return SimpleNamespace(key=key, fields=fields, changelog=SimpleNamespace(histories=histories))


def _mock_client(monkeypatch, issues):
    client = MagicMock()
    captured = {}
    def fake_search(jql, **kw):
        captured["jql"] = jql
        return issues
    client.search_issues.side_effect = fake_search
    client.current_user.return_value = "me"
    monkeypatch.setattr(jira_service, "_client", client)
    return captured


async def test_personal_scope_covers_assignee_reporter_watcher(monkeypatch):
    captured = _mock_client(monkeypatch, [_issue()])
    await jira_service.get_events_since(SINCE)  # target по умолчанию — личный канал
    jql = captured["jql"]
    assert "assignee = currentUser()" in jql
    assert "reporter = currentUser()" in jql
    assert "watcher = currentUser()" in jql


async def test_colleague_scope_is_assignee_only(monkeypatch):
    captured = _mock_client(monkeypatch, [_issue(assignee_to="jdoe")])
    events = await jira_service.get_events_since(SINCE, target="jdoe")
    jql = captured["jql"]
    assert 'assignee = "jdoe"' in jql
    assert "reporter" not in jql
    assert "watcher" not in jql
    assert any(e.event_type == "assigned" for e in events)


async def test_colleague_assigned_event_only_for_target(monkeypatch):
    _mock_client(monkeypatch, [_issue(assignee_to="someone-else")])
    events = await jira_service.get_events_since(SINCE, target="jdoe")
    assert not any(e.event_type == "assigned" for e in events)
