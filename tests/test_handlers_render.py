"""Тесты для чистых утилит рендеринга в bot.handlers.tasks."""
from bot.handlers.tasks import render_grouped_by_status, format_task
from bot.services.jira import JiraTask


def _task(key: str, status: str, summary: str = "summary") -> JiraTask:
    return JiraTask(key=key, summary=summary, url=f"https://jira.test/browse/{key}", status=status)


def test_render_grouped_orders_known_statuses_first():
    tasks = [
        _task("A-1", "Backlog"),
        _task("A-2", "In Progress"),
        _task("A-3", "Custom"),
        _task("A-4", "In Progress"),
    ]
    out = render_grouped_by_status(
        tasks, "Title:", status_order=["In Progress", "Backlog"]
    )

    pos_in_progress = out.index("In Progress")
    pos_backlog = out.index("Backlog")
    pos_custom = out.index("Custom")
    assert pos_in_progress < pos_backlog < pos_custom


def test_render_grouped_includes_all_tasks():
    tasks = [
        _task("A-1", "Done"),
        _task("A-2", "Done"),
        _task("A-3", "Other"),
    ]
    out = render_grouped_by_status(tasks, "T:", status_order=["Done"])
    for key in ("A-1", "A-2", "A-3"):
        assert key in out


def test_render_grouped_empty_input_renders_only_title():
    out = render_grouped_by_status([], "Title:", status_order=["X"])
    # Без задач не должно быть ни одной ссылки на задачу
    assert "browse/" not in out
    assert "Title" in out


def test_format_task_plain():
    line = format_task(_task("A-1", "In Progress", "do it"))
    assert "A-1" in line
    assert "do it" in line
    assert "└" not in line  # без статуса/исполнителя


def test_format_task_with_status_and_assignee():
    task = JiraTask(
        key="B-2", summary="x", url="https://jira.test/browse/B-2",
        status="In Progress", assignee="Alice",
    )
    line = format_task(task, show_status=True, show_assignee=True)
    assert "Alice" in line
    assert "In Progress" in line
    assert "└" in line


def test_format_task_unassigned_fallback():
    line = format_task(_task("C-1", "Open"), show_assignee=True)
    assert "Unassigned" in line
