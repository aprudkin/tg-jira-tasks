from dataclasses import dataclass

from jira import JIRA

from bot.config import settings


@dataclass
class JiraTask:
    """Представление задачи Jira."""

    key: str
    summary: str
    url: str
    status: str


class JiraService:
    """Сервис для работы с Jira API."""

    # Максимальное количество задач для выборки
    MAX_RESULTS = 100

    def __init__(self) -> None:
        self._client = None
        # Проверяем наличие конфигурации при инициализации, но не подключаемся
        if not (settings.jira_pat or (settings.jira_email and settings.jira_api_token)):
            raise ValueError("Jira configuration missing: enable JIRA_PAT or JIRA_EMAIL/JIRA_API_TOKEN")

    @property
    def client(self) -> JIRA:
        """Ленивая инициализация клиента Jira."""
        if self._client is None:
            if settings.jira_pat:
                self._client = JIRA(
                    server=settings.jira_url,
                    token_auth=settings.jira_pat,
                )
            else:
                self._client = JIRA(
                    server=settings.jira_url,
                    basic_auth=(settings.jira_email, settings.jira_api_token),
                )
        return self._client

    def get_my_tasks_in_progress(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в статусе 'In Progress'."""
        jql = 'assignee = currentUser() AND status = "In Progress"'
        return self._search_issues(jql)

    def get_my_tasks_in_sprint(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в активных спринтах."""
        jql = 'assignee = currentUser() AND sprint in openSprints() ORDER BY status ASC'
        return self._search_issues(jql)

    def _search_issues(self, jql: str) -> list[JiraTask]:
        """Выполняет поиск задач и возвращает список объектов JiraTask."""
        issues = self.client.search_issues(
            jql,
            fields=["key", "summary", "status"],
            maxResults=self.MAX_RESULTS,
        )

        return [
            JiraTask(
                key=issue.key,
                summary=issue.fields.summary,
                url=f"{settings.jira_url}/browse/{issue.key}",
                status=issue.fields.status.name,
            )
            for issue in issues
        ]


jira_service = JiraService()
