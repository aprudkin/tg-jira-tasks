"""Mock-based тесты проверки канала (NotificationService._check_channel via check_now).

Jira-источник и Bot.send_message инъектируются как фейки — вся логика дедупликации
(на канал), очистки при close и обновления last_check проверяется без сети.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots
from bot.services.jira import JiraEvent


@pytest.fixture
def svc(state_path, fake_jira):
    """NotificationService с инъектированными Jira/state, подписанным личным каналом и mock-ботом."""
    s = nots.NotificationService(jira=fake_jira, state_file=state_path)
    s._bot = AsyncMock()
    s._chat_id = 555
    s._channels[nots.PERSONAL] = nots.Channel(
        user=nots.PERSONAL,
        interval_minutes=30,
        last_check=datetime(2026, 1, 1, 12, 0, 0),
    )
    return s


def _me(svc) -> nots.Channel:
    return svc._channels[nots.PERSONAL]


def _evt(issue_key: str, event_id: str, event_type: str = "comment", to_status: str | None = None) -> JiraEvent:
    return JiraEvent(
        issue_key=issue_key,
        issue_summary="s",
        issue_url=f"u/{issue_key}",
        event_type=event_type,
        author="bob",
        author_id="bob",
        details="d",
        id=event_id,
        to_status=to_status,
    )


@pytest.mark.asyncio
async def test_first_check_sends_all_events(svc, fake_jira):
    fake_jira.get_events_since.return_value = [_evt("X-1", "c1"), _evt("X-1", "c2")]

    await svc.check_now()

    assert svc._bot.send_message.await_count == 2
    assert _me(svc).processed_events["X-1"] == {"c1", "c2"}


@pytest.mark.asyncio
async def test_dedup_skips_already_processed(svc, fake_jira):
    _me(svc).processed_events["X-1"] = {"c1"}
    fake_jira.get_events_since.return_value = [_evt("X-1", "c1"), _evt("X-1", "c2")]

    await svc.check_now()

    assert svc._bot.send_message.await_count == 1
    assert _me(svc).processed_events["X-1"] == {"c1", "c2"}


@pytest.mark.asyncio
async def test_close_status_keeps_dedup_history_for_replay_window(svc, fake_jira):
    fake_jira.get_events_since.return_value = [
        _evt("X-1", "c1"),
        _evt("X-1", "s1", event_type="status_change", to_status="Done"),
    ]

    await svc.check_now()

    # Close не очищает ID немедленно: они нужны при close/reopen в перекрытии.
    assert _me(svc).processed_events["X-1"] == {"c1", "s1"}


@pytest.mark.asyncio
async def test_reopen_does_not_clear_dedup_history(svc, fake_jira):
    """Регрессия: substring-проверка раньше ловила Resolved → Reopened."""
    fake_jira.get_events_since.return_value = [
        _evt("X-1", "c1"),
        _evt("X-1", "s1", event_type="status_change", to_status="Reopened"),
    ]

    await svc.check_now()

    assert "X-1" in _me(svc).processed_events
    assert {"c1", "s1"}.issubset(_me(svc).processed_events["X-1"])


@pytest.mark.asyncio
async def test_last_check_advances_to_prefetch_window_end(svc, fake_jira, monkeypatch):
    initial = _me(svc).last_check
    window_end = datetime(2026, 1, 1, 12, 5, 0, 123456)
    monkeypatch.setattr(nots, "utc_now_naive", lambda: window_end)
    fake_jira.get_events_since.return_value = []

    await svc.check_now()

    fake_jira.get_events_since.assert_awaited_once_with(
        initial - nots.EVENT_REPLAY_OVERLAP,
        None,
        until=window_end,
    )
    assert _me(svc).last_check == window_end
    svc._bot.send_message.assert_not_awaited()
    restored = nots.NotificationService(state_file=svc._state_path())
    assert _me(restored).last_check == window_end


@pytest.mark.asyncio
async def test_check_skipped_without_subscription(svc, fake_jira):
    svc._chat_id = None

    await svc.check_now()

    fake_jira.get_events_since.assert_not_awaited()
    svc._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_rate_limit_triggers_retry(svc, fake_jira, monkeypatch):
    """При TelegramRetryAfter ждём retry_after секунд и повторяем send_message."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    sleep_calls: list[float] = []
    real_sleep = nots.asyncio.sleep
    async def fake_sleep(d):
        sleep_calls.append(d)
        # Не блокируем тест на реальные секунды
        await real_sleep(0)
    monkeypatch.setattr("bot.services.notifications.asyncio.sleep", fake_sleep)

    call_count = 0
    async def flaky_send(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TelegramRetryAfter(method=SendMessage(chat_id=0, text=""), message="slow", retry_after=7)
    svc._bot.send_message = AsyncMock(side_effect=flaky_send)

    fake_jira.get_events_since.return_value = [_evt("X-1", "c1")]

    await svc.check_now()

    assert call_count == 2  # первый — 429, второй — успех
    assert 7 in sleep_calls
    assert _me(svc).processed_events["X-1"] == {"c1"}


@pytest.mark.asyncio
async def test_telegram_rate_limit_gives_up_after_bounded_retries(svc, fake_jira, monkeypatch):
    """Повторные 429 не подтверждают событие и не создают бесконечный цикл."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    real_sleep = nots.asyncio.sleep
    async def fake_sleep(d):
        await real_sleep(0)
    monkeypatch.setattr("bot.services.notifications.asyncio.sleep", fake_sleep)

    async def always_429(*a, **kw):
        raise TelegramRetryAfter(method=SendMessage(chat_id=0, text=""), message="slow", retry_after=1)
    svc._bot.send_message = AsyncMock(side_effect=always_429)

    fake_jira.get_events_since.return_value = [_evt("X-1", "c1")]

    await svc.check_now()

    assert svc._bot.send_message.await_count == nots.SEND_MAX_ATTEMPTS
    assert "c1" not in _me(svc).processed_events["X-1"]


@pytest.mark.asyncio
async def test_silent_user_disables_notification_sound(svc, fake_jira):
    svc._silent_users = {"bob"}
    fake_jira.get_events_since.return_value = [_evt("X-1", "c1")]

    await svc.check_now()

    call = svc._bot.send_message.await_args
    assert call.kwargs["disable_notification"] is True
    assert call.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_long_notification_chunks_preserve_text_and_options(svc, fake_jira):
    event = _evt("X-1", "c1")
    event.issue_summary = "😀" * 2500 + " <component> & review"
    event.issue_url = "https://jira.test/browse/X-1?value=" + "a" * 5000
    event.author = '<Alice & "Bob">'
    event.details = '<a href="bad">click</a>'
    svc._silent_users = {"bob"}
    fake_jira.get_events_since.return_value = [event]

    expected_text = svc._format_event(event).render()[0]
    await svc.check_now()

    assert svc._bot.send_message.await_count >= 2
    sent_text = "".join(call.kwargs["text"] for call in svc._bot.send_message.await_args_list)
    assert sent_text == expected_text
    for call in svc._bot.send_message.await_args_list:
        assert call.kwargs["parse_mode"] is None
        assert call.kwargs["disable_notification"] is True
        assert len(call.kwargs["text"].encode("utf-16-le")) // 2 <= 4096
    assert _me(svc).processed_events["X-1"] == {"c1"}


@pytest.mark.asyncio
async def test_long_notification_retries_only_failed_chunk(svc, fake_jira, monkeypatch):
    """Уже отправленный фрагмент не дублируется при 429 на следующем."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    event = _evt("X-1", "c1")
    event.details = "x" * 5000
    fake_jira.get_events_since.return_value = [event]

    real_sleep = nots.asyncio.sleep
    async def fake_sleep(delay):
        await real_sleep(0)
    monkeypatch.setattr("bot.services.notifications.asyncio.sleep", fake_sleep)

    attempts: list[str] = []
    failed_second = False
    async def fail_second_once(*args, **kwargs):
        nonlocal failed_second
        attempts.append(kwargs["text"])
        if len(attempts) == 2 and not failed_second:
            failed_second = True
            raise TelegramRetryAfter(
                method=SendMessage(chat_id=0, text=""), message="slow", retry_after=1
            )

    expected_chunks = [chunk.text for chunk in nots.split_message(svc._format_event(event))]
    svc._bot.send_message = AsyncMock(side_effect=fail_second_once)
    await svc.check_now()

    assert attempts == [expected_chunks[0], expected_chunks[1], *expected_chunks[1:]]


@pytest.mark.asyncio
async def test_delivery_failure_keeps_cursor_and_event_unconfirmed(svc, fake_jira):
    initial = _me(svc).last_check
    fake_jira.get_events_since.return_value = [_evt("X-1", "c1")]
    svc._bot.send_message = AsyncMock(side_effect=OSError("Telegram unavailable"))

    await svc.check_now()

    assert _me(svc).last_check == initial
    assert "c1" not in _me(svc).processed_events["X-1"]


@pytest.mark.asyncio
async def test_partial_delivery_persists_only_successes_and_retries_failure(
    svc, fake_jira, monkeypatch
):
    initial = _me(svc).last_check
    events = [_evt("X-1", "ok"), _evt("X-1", "bad"), _evt("X-1", "also-ok")]
    fake_jira.get_events_since.return_value = events
    svc._bot.send_message = AsyncMock(
        side_effect=[None, *[OSError("timeout")] * nots.SEND_MAX_ATTEMPTS, None]
    )
    real_sleep = nots.asyncio.sleep

    async def fake_sleep(delay):
        await real_sleep(0)

    monkeypatch.setattr(nots.asyncio, "sleep", fake_sleep)
    await svc.check_now()

    assert _me(svc).last_check == initial
    assert _me(svc).processed_events["X-1"] == {"ok", "also-ok"}
    svc._bot.send_message = AsyncMock()
    await svc.check_now()
    assert svc._bot.send_message.await_count == 1
    assert _me(svc).processed_events["X-1"] == {"ok", "bad", "also-ok"}


@pytest.mark.asyncio
async def test_state_write_failure_does_not_confirm_cursor(svc, fake_jira, monkeypatch):
    initial = _me(svc).last_check
    fake_jira.get_events_since.return_value = [_evt("X-1", "c1")]

    def fail_write(payload):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_state", fail_write)
    await svc.check_now()

    assert _me(svc).last_check == initial
    assert "c1" in _me(svc).processed_events["X-1"]


@pytest.mark.asyncio
async def test_replay_dedup_survives_close_and_reopen(svc, fake_jira):
    close = _evt("X-1", "close", event_type="status_change", to_status="Done")
    reopen = _evt("X-1", "reopen", event_type="status_change", to_status="Reopened")
    fake_jira.get_events_since.return_value = [close, reopen]
    await svc.check_now()
    sent = svc._bot.send_message.await_count

    # Следующий запрос возвращает перекрывающийся close/reopen, но дублей нет.
    await svc.check_now()
    assert svc._bot.send_message.await_count == sent


@pytest.mark.asyncio
async def test_dedup_retention_is_bounded_by_replay_window(svc, fake_jira, monkeypatch):
    old = _evt("X-1", "old")
    old.timestamp = _me(svc).last_check - nots.DEDUP_RETENTION - timedelta(seconds=1)
    _me(svc).processed_events["X-1"] = {"old"}
    _me(svc).processed_event_times["X-1"] = {"old": old.timestamp}
    window_end = _me(svc).last_check + timedelta(minutes=1)
    monkeypatch.setattr(nots, "utc_now_naive", lambda: window_end)
    fake_jira.get_events_since.return_value = []

    await svc.check_now()

    assert "X-1" not in _me(svc).processed_events


@pytest.mark.asyncio
async def test_more_than_fifty_event_ids_remain_deduplicated(
    svc, fake_jira, monkeypatch
):
    events = [_evt("X-1", f"event-{number}") for number in range(51)]
    fake_jira.get_events_since.return_value = events

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(nots.asyncio, "sleep", no_sleep)
    await svc.check_now()
    await svc.check_now()

    assert svc._bot.send_message.await_count == 51
    assert len(_me(svc).processed_events["X-1"]) == 51
