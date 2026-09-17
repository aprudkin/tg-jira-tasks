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
def test_empty_allowed_users_requires_explicit_open_access(allowed_users):
    with pytest.raises(ValidationError) as exc_info:
        Settings(**BASE_SETTINGS, allowed_users=allowed_users)

    assert "ALLOW_OPEN_ACCESS" in str(exc_info.value)


def test_explicit_open_access_allows_empty_user_list():
    configured = Settings(
        **BASE_SETTINGS, allowed_users="", allow_open_access=True
    )

    assert configured.allowed_user_ids == []
    assert configured.allow_open_access is True


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


def test_allowed_group_chat_ids_are_validated_and_parsed():
    configured = Settings(
        **BASE_SETTINGS,
        allowed_users="123",
        allowed_chat_ids=" -100123, -456 ",
    )

    assert configured.allowed_group_chat_ids == [-100123, -456]


@pytest.mark.parametrize(
    "allowed_chat_ids",
    ["0", "123", "-100123,0", "-100123,123", "-100123,, -456", "group"],
)
def test_invalid_allowed_group_chat_ids_fail_at_startup(allowed_chat_ids):
    with pytest.raises(ValidationError):
        Settings(
            **BASE_SETTINGS,
            allowed_users="123",
            allowed_chat_ids=allowed_chat_ids,
        )


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
