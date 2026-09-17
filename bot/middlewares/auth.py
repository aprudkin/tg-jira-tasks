from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from bot.access import AccessPolicy, access_policy


class AuthMiddleware(BaseMiddleware):
    def __init__(self, policy: AccessPolicy = access_policy) -> None:
        self._policy = policy

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if not self._policy.allows_message(user_id, event.chat.id, event.chat.type):
            await event.answer("Access denied.")
            return None

        return await handler(event, data)
