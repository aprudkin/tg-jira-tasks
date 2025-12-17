from dataclasses import dataclass

from jira import JIRA

from bot.config import settings


@dataclass
class JiraTask:
    """Представление задачи Jira."""

    key: str
    summary: str
    url: str


class JiraService:
    """Сервис для работы с Jira API."""

    # Максимальное количество задач для выборки
    MAX_RESULTS = 100

    def __init__(self) -> None:
        self.client = JIRA(
            server=settings.jira_url,
            basic_auth=(settings.jira_email, settings.jira_api_token),
        )

    def get_my_tasks_in_progress(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в статусе 'In Progress'."""
        jql = 'assignee = currentUser() AND status = "In Progress"'
        # Запрашиваем только необходимые поля для оптимизации
        issues = self.client.search_issues(
            jql,
            fields=["key", "summary"],
            maxResults=self.MAX_RESULTS,
        )

        return [
            JiraTask(
                key=issue.key,
                summary=issue.fields.summary,
                url=f"{settings.jira_url}/browse/{issue.key}",
            )
            for issue in issues
        ]


jira_service = JiraService()
