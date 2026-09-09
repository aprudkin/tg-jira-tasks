"""Тесты общего рендера и безопасной ссылки на задачу."""
from bot.render import issue_ref


def test_issue_ref_links_key_and_appends_summary():
    url = "https://jira.test/browse/ABC-1"
    out = issue_ref("ABC-1", url, "Fix the thing")
    text, entities = out.render()

    assert text == "ABC-1: Fix the thing"
    assert len(entities) == 1
    assert entities[0].type == "text_link"
    assert entities[0].url == url
    assert entities[0].extract_from(text) == "ABC-1"


def test_issue_ref_treats_html_characters_as_plain_text():
    key = '<A&"1>'
    summary = "Fix <component> & 'review'"
    url = 'https://jira.test/browse/A-1?x=<tag>&quote="yes"'
    out = issue_ref(key, url, summary)
    text, entities = out.render()

    assert text == f"{key}: {summary}"
    assert entities[0].url == url
    assert entities[0].extract_from(text) == key
