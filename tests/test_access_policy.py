"""Regression matrix for command and restored-delivery access policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.access import AccessPolicy
from bot.middlewares.auth import AuthMiddleware
from bot.services.notifications import NotificationService, PERSONAL


def _policy(
    *, users: tuple[int, ...] = (100,), chats: tuple[int, ...] = (), open_access=False
) -> AccessPolicy:
    return AccessPolicy(
        allowed_user_ids=frozenset(users),
        allowed_chat_ids=frozenset(chats),
        allow_open_access=open_access,
    )


def _message(user_id: int | None, chat_id: int, chat_type: str):
    return SimpleNamespace(
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        answer=AsyncMock(),
    )


@pytest.mark.parametrize(
    ("policy", "user_id", "chat_id", "chat_type", "allowed"),
    [
        (_policy(), 100, 100, "private", True),
        (_policy(), 200, 200, "private", False),
        (_policy(), None, 100, "private", False),
        (_policy(), 100, 200, "private", False),
        (_policy(chats=(-1000,)), 100, -1000, "group", True),
        (_policy(chats=(-1000,)), 100, -1000, "supergroup", True),
        (_policy(), 100, -1000, "group", False),
        (_policy(chats=(-1000,)), 200, -1000, "group", False),
        (_policy(open_access=True), 200, 200, "private", True),
        (_policy(open_access=True), 200, -1000, "group", False),
        (_policy(chats=(-1000,), open_access=True), 200, -1000, "group", True),
        (_policy(chats=(-1000,), open_access=True), None, -1000, "group", False),
        (_policy(chats=(-1000,), open_access=True), 200, -1000, "channel", False),
    ],
)
async def test_auth_middleware_applies_sender_and_chat_policy(
    policy, user_id, chat_id, chat_type, allowed
):
    middleware = AuthMiddleware(policy)
    message = _message(user_id, chat_id, chat_type)
    handler = AsyncMock(return_value="handled")

    result = await middleware(handler, message, {})

    if allowed:
        assert result == "handled"
        handler.assert_awaited_once_with(message, {})
        message.answer.assert_not_awaited()
    else:
        assert result is None
        handler.assert_not_awaited()
        message.answer.assert_awaited_once_with("Access denied.")


def test_background_delivery_uses_chat_identity_policy():
    restricted = _policy(users=(100,), chats=(-1000,))
    opened = _policy(users=(), chats=(-1000,), open_access=True)

    assert restricted.allows_delivery(100)
    assert not restricted.allows_delivery(200)
    assert restricted.allows_delivery(-1000)
    assert not restricted.allows_delivery(-2000)
    assert opened.allows_delivery(200)
    assert opened.allows_delivery(-1000)
    assert not opened.allows_delivery(-2000)


@pytest.mark.parametrize(
    ("chat_id", "denying_policy"),
    [
        (321, _policy(users=(100,))),
        (-1000321, _policy(users=(100,), chats=(-1000,))),
    ],
)
async def test_restored_forbidden_chat_keeps_state_but_never_fetches_or_delivers(
    state_path, fake_jira, caplog, chat_id, denying_policy
):
    initial = NotificationService(jira=fake_jira, state_file=state_path)
    assert await initial.subscribe(chat_id, 15)
    original_state = state_path.read_text()

    restored_jira = AsyncMock()
    restored_jira.get_events_since = AsyncMock(return_value=[])
    restored = NotificationService(
        jira=restored_jira,
        state_file=state_path,
        first_check_delay=0,
        policy=denying_policy,
    )
    bot = AsyncMock()

    restored.start(bot)
    await restored.check_now()
    await restored.stop()

    assert restored.get_channel(PERSONAL) is not None
    assert state_path.read_text() == original_state
    assert restored._tasks == {}
    restored_jira.get_events_since.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert str(chat_id) not in caplog.text
    assert "not allowed by access policy" in caplog.text
