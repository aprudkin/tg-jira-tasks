"""Тесты UTF-16-aware разбиения и отправки форматированных сообщений."""
from unittest.mock import AsyncMock, MagicMock

from aiogram.utils.formatting import Bold, Text, TextLink
import pytest

from bot.handlers.tasks import _answer_chunked
from bot.render import TG_MESSAGE_LIMIT, split_message


def _msg():
    message = MagicMock()
    message.answer = AsyncMock()
    return message


def _utf16_size(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_short_text_sent_as_single_message():
    chunks = split_message("hello\nworld")
    assert len(chunks) == 1
    assert chunks[0].text == "hello\nworld"
    assert chunks[0].entities == []


def test_chunks_preserve_all_content_and_limit():
    text = "\n".join(["x" * 100] * 50)
    chunks = split_message(text)
    assert len(chunks) >= 2
    assert "".join(chunk.text for chunk in chunks) == text
    assert all(_utf16_size(chunk.text) <= TG_MESSAGE_LIMIT for chunk in chunks)


def test_exact_utf16_limit_and_one_emoji_over():
    exact = "😀" * (TG_MESSAGE_LIMIT // 2)
    assert len(split_message(exact)) == 1

    chunks = split_message(exact + "😀")
    assert len(chunks) == 2
    assert "".join(chunk.text for chunk in chunks) == exact + "😀"
    assert all(_utf16_size(chunk.text) <= TG_MESSAGE_LIMIT for chunk in chunks)


def test_long_line_splits_without_breaking_emoji_cluster():
    family = "👨‍👩‍👧‍👦"
    text = "x" * (TG_MESSAGE_LIMIT - 2) + family + "tail"
    chunks = split_message(text)

    assert "".join(chunk.text for chunk in chunks) == text
    assert chunks[0].text.endswith("x")
    assert chunks[1].text.startswith(family)


def test_entities_are_rebased_and_preserved_across_chunks():
    long_url = "https://jira.test/browse/X-1?value=" + "a" * 5000
    content = Text(Bold("x" * 5000), "\n", TextLink("<X&1>", url=long_url))
    chunks = split_message(content)

    assert "".join(chunk.text for chunk in chunks) == "x" * 5000 + "\n<X&1>"
    assert all(_utf16_size(chunk.text) <= TG_MESSAGE_LIMIT for chunk in chunks)
    for chunk in chunks:
        for entity in chunk.entities:
            assert entity.offset >= 0
            assert entity.length > 0
            assert entity.offset + entity.length <= _utf16_size(chunk.text)

    bold_parts = [
        entity.extract_from(chunk.text)
        for chunk in chunks
        for entity in chunk.entities
        if entity.type == "bold"
    ]
    assert "".join(bold_parts) == "x" * 5000

    link_entities = [
        (chunk, entity)
        for chunk in chunks
        for entity in chunk.entities
        if entity.type == "text_link"
    ]
    assert len(link_entities) == 1
    link_chunk, link_entity = link_entities[0]
    assert link_entity.url == long_url
    assert link_entity.extract_from(link_chunk.text) == "<X&1>"


def test_newline_after_utf16_limit_does_not_overflow_chunk():
    chunks = split_message("😀aaa\nx", limit=5)
    assert "".join(chunk.text for chunk in chunks) == "😀aaa\nx"
    assert all(_utf16_size(chunk.text) <= 5 for chunk in chunks)


def test_limit_too_small_for_code_point_is_rejected():
    with pytest.raises(ValueError, match="Unicode code point"):
        split_message("😀", limit=1)


@pytest.mark.asyncio
async def test_answer_chunked_disables_parse_mode_for_every_chunk():
    message = _msg()
    await _answer_chunked(message, Text(Bold("x" * 5000)))

    assert message.answer.await_count == 2
    for call in message.answer.await_args_list:
        assert call.kwargs["parse_mode"] is None
        assert _utf16_size(call.kwargs["text"]) <= TG_MESSAGE_LIMIT
        assert call.kwargs["entities"]
