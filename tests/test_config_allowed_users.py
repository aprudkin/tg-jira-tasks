"""Startup validation tests for the Telegram user whitelist."""
import pytest
from pydantic import ValidationError

from bot.config import Settings


BASE_SETTINGS = {
    "telegram_token": "test-telegram-token",
    "jira_url": "http://jira.test",
    "jira_pat": "test-jira-pat",
}


def test_allowed_users_are_validated_and_parsed_at_settings_construction():
    configured = Settings(**BASE_SETTINGS, allowed_users=" 123, 456,789 ")

    assert configured.allowed_user_ids == [123, 456, 789]


@pytest.mark.parametrize("allowed_users", ["", " ", "\t\n"])
def test_empty_or_whitespace_only_allowed_users_allows_everyone(allowed_users):
    configured = Settings(**BASE_SETTINGS, allowed_users=allowed_users)

    assert configured.allowed_user_ids == []


@pytest.mark.parametrize(
    "allowed_users",
    [
        "0",
        "-1",
        "123,0",
        "123,-456",
        "123,,456",
        "123, ,456",
        "123,",
        ",123",
        ",",
        "123,not-a-telegram-id,456",
    ],
)
def test_invalid_allowed_users_fail_during_settings_construction(allowed_users):
    with pytest.raises(ValidationError):
        Settings(**BASE_SETTINGS, allowed_users=allowed_users)


def test_invalid_allowed_users_error_does_not_expose_input_or_secrets():
    invalid_whitelist = "123,private-invalid-value"
    telegram_secret = "telegram-secret-that-must-not-leak"
    jira_secret = "jira-secret-that-must-not-leak"

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            telegram_token=telegram_secret,
            jira_url="http://jira.test",
            jira_pat=jira_secret,
            allowed_users=invalid_whitelist,
        )

    error = str(exc_info.value)
    assert invalid_whitelist not in error
    assert telegram_secret not in error
    assert jira_secret not in error
    assert "ALLOWED_USERS" in error
