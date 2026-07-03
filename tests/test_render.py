"""Тест общего рендера ссылки на задачу (bot.render)."""
from bot.render import issue_ref


def test_issue_ref_links_key_and_appends_summary():
    out = issue_ref("ABC-1", "https://jira.test/browse/ABC-1", "Fix the thing")
    assert "ABC-1" in out
    assert "https://jira.test/browse/ABC-1" in out
    assert out.endswith(": Fix the thing")
