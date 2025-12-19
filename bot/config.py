import functools
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    telegram_token: str
    jira_url: str
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_pat: str | None = None
    allowed_users: str = ""

    @functools.cached_property
    def allowed_user_ids(self) -> list[int]:
        """Преобразует строку allowed_users в список ID пользователей."""
        if not self.allowed_users:
            return []
        return [int(uid.strip()) for uid in self.allowed_users.split(",") if uid.strip()]


settings = Settings()
