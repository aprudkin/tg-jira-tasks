from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from bot.config import settings


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if not settings.allowed_user_ids:
            return await handler(event, data)

        if user_id not in settings.allowed_user_ids:
            await event.answer("Access denied. Your Telegram ID is not in the whitelist.")
            return None

        return await handler(event, data)
