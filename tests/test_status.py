"""Тесты словаря статусов bot.status — единого владельца имён, порядка и групп."""
from bot import status


def test_order_contains_every_group_member():
    """Каждый статус из семантических групп присутствует в каноническом порядке."""
    for group in (status.BACKLOG_GROUP, status.WAITING_GROUP, status.CLOSED_GROUP):
        for name in group:
            assert name in status.ORDER


def test_on_hold_ranks_above_resolved():
    """Регрессия дрейфа 'Hold'/'On Hold': On Hold — ждущий статус, стоит выше завершённых."""
    assert status.ORDER.index(status.ON_HOLD) < status.ORDER.index(status.RESOLVED)


def test_closed_group_membership():
    """Sanity: множество закрытых статусов не сузили и не расширили (мигрировано из test_jira_helpers)."""
    assert status.DONE in status.CLOSED_GROUP
    assert status.CLOSED in status.CLOSED_GROUP
    assert status.RESOLVED in status.CLOSED_GROUP
    # Reopened — НЕ закрытый, иначе дедуп ломается.
    assert status.REOPENED not in status.CLOSED_GROUP
    assert status.IN_PROGRESS not in status.CLOSED_GROUP


def test_waiting_group_is_discussion_and_on_hold():
    assert status.WAITING_GROUP == (status.DISCUSSION, status.ON_HOLD)


def test_backlog_group_is_todo_backlog_open():
    assert status.BACKLOG_GROUP == (status.TO_DO, status.BACKLOG, status.OPEN)
