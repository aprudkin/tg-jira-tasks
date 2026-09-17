"""Secret-safe deployment configuration diagnostic."""

from bot.config import settings


def main() -> int:
    missing: list[str] = []
    if not settings.telegram_token.strip():
        missing.append("TELEGRAM_TOKEN")
    if not settings.jira_url.strip():
        missing.append("JIRA_URL")
    if not (
        (settings.jira_pat and settings.jira_pat.strip())
        or (
            settings.jira_email
            and settings.jira_email.strip()
            and settings.jira_api_token
            and settings.jira_api_token.strip()
        )
    ):
        missing.append("JIRA_PAT or JIRA_EMAIL/JIRA_API_TOKEN")

    if missing:
        print("Configuration invalid; missing or empty: " + ", ".join(missing))
        return 1

    # Do not print values, lengths, URLs, user IDs, or token-derived data.
    print("Configuration valid; required settings and Jira authentication are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
