"""Общие помощники рендеринга Telegram-сообщений.

Форматирование строится через Telegram entities, а не через HTML. Это позволяет
безопасно вставлять данные Jira и делить длинные сообщения без разрыва разметки.
"""
from bisect import bisect_right
from dataclasses import dataclass
import unicodedata

from aiogram.types import MessageEntity
from aiogram.utils.formatting import Text, TextLink

TG_MESSAGE_LIMIT = 4096
_ZERO_WIDTH_JOINER = "\u200d"


@dataclass(frozen=True)
class MessageChunk:
    """Самодостаточный фрагмент сообщения с локальными смещениями entities."""

    text: str
    entities: list[MessageEntity]

    def as_kwargs(self) -> dict:
        """Аргументы для методов отправки aiogram без глобального parse mode."""
        return {"text": self.text, "entities": self.entities, "parse_mode": None}


def issue_ref(key: str, url: str, summary: str) -> Text:
    """Ссылка на задачу одной строкой: «ключ: краткое описание»."""
    return Text(TextLink(key, url=url), ": ", summary)


def join_text(items: list[Text | str], separator: str = "\n") -> Text:
    """Объединяет текстовые узлы, не превращая entities в строковую разметку."""
    body: list[Text | str] = []
    for index, item in enumerate(items):
        if index:
            body.append(separator)
        body.append(item)
    return Text(*body)


def _is_regional_indicator(char: str) -> bool:
    return "\U0001f1e6" <= char <= "\U0001f1ff"


def _is_unicode_continuation(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.combining(char) != 0
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _is_safe_boundary(text: str, index: int) -> bool:
    """Проверяет, что граница не разрывает распространённый emoji/grapheme-кластер."""
    if index <= 0 or index >= len(text):
        return True
    if text[index - 1] == _ZERO_WIDTH_JOINER or text[index] == _ZERO_WIDTH_JOINER:
        return False
    if _is_unicode_continuation(text[index]):
        return False
    if _is_regional_indicator(text[index - 1]) and _is_regional_indicator(text[index]):
        preceding = 0
        cursor = index - 1
        while cursor >= 0 and _is_regional_indicator(text[cursor]):
            preceding += 1
            cursor -= 1
        return preceding % 2 == 0
    return True


def _preferred_boundary(text: str, start: int, maximum: int) -> int:
    """Выбирает границу: строка, пробел, затем безопасная Unicode-граница."""
    newline = text.rfind("\n", start, maximum)
    if newline >= start:
        return newline + 1

    whitespace = maximum
    while whitespace > start and not text[whitespace - 1].isspace():
        whitespace -= 1
    if whitespace > start:
        return whitespace

    boundary = maximum
    while boundary > start and not _is_safe_boundary(text, boundary):
        boundary -= 1
    # Патологически длинный кластер всё равно приходится делить из-за лимита Telegram.
    return boundary if boundary > start else maximum


def split_message(content: Text | str, limit: int = TG_MESSAGE_LIMIT) -> list[MessageChunk]:
    """Делит сообщение по UTF-16 units и переносит entities в локальные координаты."""
    if limit < 1:
        raise ValueError("message chunk limit must be positive")

    rendered = content if isinstance(content, Text) else Text(content)
    text, entities = rendered.render()
    if not text:
        return []

    # offsets[i] — длина text[:i] в UTF-16 code units, как требует Telegram.
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + len(char.encode("utf-16-le")) // 2)

    chunks: list[MessageChunk] = []
    start = 0
    while start < len(text):
        maximum = bisect_right(offsets, offsets[start] + limit) - 1
        if maximum == start:
            raise ValueError("message chunk limit is smaller than one Unicode code point")
        end = len(text) if maximum >= len(text) else _preferred_boundary(text, start, maximum)
        unit_start = offsets[start]
        unit_end = offsets[end]

        chunk_entities: list[MessageEntity] = []
        for entity in entities:
            entity_start = entity.offset
            entity_end = entity.offset + entity.length
            overlap_start = max(entity_start, unit_start)
            overlap_end = min(entity_end, unit_end)
            if overlap_start < overlap_end:
                chunk_entities.append(
                    entity.model_copy(
                        update={
                            "offset": overlap_start - unit_start,
                            "length": overlap_end - overlap_start,
                        }
                    )
                )

        chunks.append(MessageChunk(text[start:end], chunk_entities))
        start = end

    return chunks
