import functools
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
    )

    telegram_token: str
    jira_url: str
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_pat: str | None = None
    allowed_users: str = ""
    # Путь к файлу состояния подписки на уведомления.
    # В Docker монтируется через volume, локально можно переопределить через env STATE_FILE.
    state_file: Path = Path("/app/data/sync_state.json")

    @staticmethod
    def _parse_allowed_user_ids(value: str) -> list[int]:
        if not value.strip():
            return []

        components = value.split(",")
        if any(not component.strip() for component in components):
            raise ValueError("ALLOWED_USERS contains an empty ID")

        user_ids = [int(component.strip()) for component in components]
        if any(user_id <= 0 for user_id in user_ids):
            raise ValueError("ALLOWED_USERS IDs must be positive")
        return user_ids

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: str) -> str:
        """Отклоняет некорректный whitelist при создании Settings, до запуска бота."""
        try:
            cls._parse_allowed_user_ids(value)
        except ValueError:
            raise ValueError(
                "ALLOWED_USERS must be a comma-separated list of positive integer IDs"
            ) from None
        return value

    @functools.cached_property
    def allowed_user_ids(self) -> list[int]:
        """Преобразует проверенную строку allowed_users в список ID пользователей."""
        return self._parse_allowed_user_ids(self.allowed_users)


settings = Settings()
