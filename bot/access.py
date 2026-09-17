"""Shared Telegram access policy for commands and background delivery."""

from dataclasses import dataclass

from bot.config import settings


@dataclass(frozen=True)
class AccessPolicy:
    """Authorize Telegram senders, command chats, and persisted destinations."""

    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    allow_open_access: bool = False

    def allows_user(self, user_id: int | None) -> bool:
        """Missing senders are always denied; open access permits known senders."""
        return user_id is not None and (
            self.allow_open_access or user_id in self.allowed_user_ids
        )

    def allows_message(
        self, user_id: int | None, chat_id: int, chat_type: object
    ) -> bool:
        """Authorize a command using both its sender and destination chat."""
        if not self.allows_user(user_id):
            return False

        normalized_type = getattr(chat_type, "value", chat_type)
        if normalized_type == "private":
            return chat_id > 0 and chat_id == user_id
        if normalized_type in {"group", "supergroup"}:
            return chat_id in self.allowed_chat_ids
        return False

    def allows_delivery(self, chat_id: int) -> bool:
        """Authorize a persisted subscribed chat without an incoming sender."""
        if chat_id > 0:
            return self.allow_open_access or chat_id in self.allowed_user_ids
        if chat_id < 0:
            return chat_id in self.allowed_chat_ids
        return False


access_policy = AccessPolicy(
    allowed_user_ids=frozenset(settings.allowed_user_ids),
    allowed_chat_ids=frozenset(settings.allowed_group_chat_ids),
    allow_open_access=settings.allow_open_access,
)
