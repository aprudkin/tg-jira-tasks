"""Шов №6: разбор аргументов /track — чистая функция parse_track_args.

Контракт: parse_track_args(args) -> (user, emoji, interval).
Токены после user определяются по типу: число → интервал, иначе → маркер-эмодзи.
Порядок эмодзи/интервала не важен. Пустой ввод, нулевой интервал и не-эмодзи маркер — ошибка.
"""
import pytest

from bot.handlers.tasks import parse_track_args


def test_full_user_emoji_interval():
    assert parse_track_args("jdoe 🔵 15") == ("jdoe", "🔵", 15)


def test_user_and_interval_only():
    assert parse_track_args("jdoe 15") == ("jdoe", None, 15)


def test_user_and_emoji_only():
    assert parse_track_args("jdoe 🔵") == ("jdoe", "🔵", None)


def test_user_only():
    assert parse_track_args("jdoe") == ("jdoe", None, None)


def test_order_of_emoji_and_interval_is_flexible():
    assert parse_track_args("jdoe 15 🔵") == ("jdoe", "🔵", 15)


def test_extra_whitespace_tolerated():
    assert parse_track_args("  jdoe   🔵   15  ") == ("jdoe", "🔵", 15)


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_track_args("")


def test_zero_interval_raises():
    with pytest.raises(ValueError):
        parse_track_args("jdoe 0")


def test_non_emoji_marker_raises():
    with pytest.raises(ValueError):
        parse_track_args("jdoe abc")
