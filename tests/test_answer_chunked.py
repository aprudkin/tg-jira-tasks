"""Тесты для _answer_chunked в bot.handlers.tasks."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.tasks import _answer_chunked, TG_MESSAGE_CHUNK_SIZE


def _msg():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_short_text_sent_as_single_message():
    msg = _msg()
    await _answer_chunked(msg, "hello\nworld")
    assert msg.answer.await_count == 1
    assert msg.answer.await_args.args == ("hello\nworld",)


@pytest.mark.asyncio
async def test_long_text_split_on_newlines():
    msg = _msg()
    # Каждая строка ~100 символов, всего > 4000 → требуется чанкинг
    line = "x" * 100
    text = "\n".join([line] * 50)  # ~5050 chars
    await _answer_chunked(msg, text)
    assert msg.answer.await_count >= 2
    # Каждый чанк ≤ лимит
    for call in msg.answer.await_args_list:
        sent = call.args[0]
        assert len(sent) <= TG_MESSAGE_CHUNK_SIZE


@pytest.mark.asyncio
async def test_chunks_preserve_all_content():
    msg = _msg()
    line = "x" * 100
    text = "\n".join([line] * 50)
    await _answer_chunked(msg, text)
    rejoined = "\n".join(c.args[0] for c in msg.answer.await_args_list)
    assert rejoined == text


@pytest.mark.asyncio
async def test_single_line_longer_than_limit_force_split():
    msg = _msg()
    text = "y" * (TG_MESSAGE_CHUNK_SIZE + 200)
    await _answer_chunked(msg, text)
    assert msg.answer.await_count == 2
    for call in msg.answer.await_args_list:
        assert len(call.args[0]) <= TG_MESSAGE_CHUNK_SIZE
    rejoined = "".join(c.args[0] for c in msg.answer.await_args_list)
    assert rejoined == text


@pytest.mark.asyncio
async def test_text_exactly_at_limit_sent_as_one():
    msg = _msg()
    text = "z" * TG_MESSAGE_CHUNK_SIZE
    await _answer_chunked(msg, text)
    assert msg.answer.await_count == 1
