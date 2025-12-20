import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jira import JIRA

from bot.config import settings

logger = logging.getLogger(__name__)


@dataclass
class JiraTask:
    """Представление задачи Jira."""

    key: str
    summary: str
    url: str
    status: str
    assignee: str | None = None


@dataclass
class JiraComment:
    """Представление комментария Jira."""

    issue_key: str
    issue_summary: str
    author: str
    body: str
    created: datetime


@dataclass
class JiraEvent:
    """Событие изменения в Jira."""

    issue_key: str
    issue_summary: str
    issue_url: str
    event_type: str  # "comment", "status_change", "assigned"
    author: str
    author_id: str
    details: str
    id: str  # Unique ID for deduplication
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class JiraStats:
    """Статистика по задачам."""

    in_progress: int
    in_backlog: int
    resolved_this_week: int
    total_assigned: int


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

    async def get_my_tasks_in_progress(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в статусе 'In Progress'."""
        return await asyncio.to_thread(self._get_my_tasks_in_progress_sync)

    def _get_my_tasks_in_progress_sync(self) -> list[JiraTask]:
        jql = 'assignee = currentUser() AND status = "In Progress"'
        return self._search_issues(jql)

    async def get_my_tasks_in_sprint(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в активных спринтах."""
        return await asyncio.to_thread(self._get_my_tasks_in_sprint_sync)

    def _get_my_tasks_in_sprint_sync(self) -> list[JiraTask]:
        jql = 'assignee = currentUser() AND sprint in openSprints() ORDER BY status ASC'
        return self._search_issues(jql)

    async def get_tasks_created_by_me(self) -> list[JiraTask]:
        """Получает незавершённые задачи, созданные мной, где исполнитель не я."""
        return await asyncio.to_thread(self._get_tasks_created_by_me_sync)

    def _get_tasks_created_by_me_sync(self) -> list[JiraTask]:
        jql = (
            'reporter = currentUser() AND assignee != currentUser() '
            'AND resolution = Unresolved ORDER BY updated DESC'
        )
        return self._search_issues(jql, include_assignee=True)

    async def get_recent_tasks(self, hours: int = 24) -> list[JiraTask]:
        """Получает задачи, обновлённые за последние N часов."""
        return await asyncio.to_thread(self._get_recent_tasks_sync, hours)

    def _get_recent_tasks_sync(self, hours: int) -> list[JiraTask]:
        jql = (
            f'assignee = currentUser() AND updated >= -{hours}h '
            'ORDER BY updated DESC'
        )
        return self._search_issues(jql)

    async def get_todo_tasks(self) -> list[JiraTask]:
        """Получает задачи в статусах To Do / Backlog."""
        return await asyncio.to_thread(self._get_todo_tasks_sync)

    def _get_todo_tasks_sync(self) -> list[JiraTask]:
        jql = (
            'assignee = currentUser() AND status in ("To Do", "Backlog", "Open") '
            'AND resolution = Unresolved ORDER BY priority DESC, created ASC'
        )
        return self._search_issues(jql)

    async def get_waiting_tasks(self) -> list[JiraTask]:
        """Получает задачи в статусах Discussion / Hold."""
        return await asyncio.to_thread(self._get_waiting_tasks_sync)

    def _get_waiting_tasks_sync(self) -> list[JiraTask]:
        jql = (
            'assignee = currentUser() AND status in ("Discussion", "Hold", "On Hold") '
            'AND resolution = Unresolved ORDER BY updated DESC'
        )
        return self._search_issues(jql)

    async def get_watching_tasks(self) -> list[JiraTask]:
        """Получает незакрытые задачи, которые я отслеживаю (watcher), где исполнитель не я."""
        return await asyncio.to_thread(self._get_watching_tasks_sync)

    def _get_watching_tasks_sync(self) -> list[JiraTask]:
        jql = (
            'watcher = currentUser() AND assignee != currentUser() '
            'AND resolution = Unresolved '
            'AND status not in (Done, Closed, Resolved) '
            'ORDER BY updated DESC'
        )
        return self._search_issues(jql, include_assignee=True)

    async def get_stats(self) -> JiraStats:
        """Получает статистику по задачам."""
        return await asyncio.to_thread(self._get_stats_sync)

    def _get_stats_sync(self) -> JiraStats:
        # Задачи в работе
        in_progress = self.client.search_issues(
            'assignee = currentUser() AND status = "In Progress"',
            maxResults=0,
        ).total

        # Задачи в бэклоге
        in_backlog = self.client.search_issues(
            'assignee = currentUser() AND status in ("To Do", "Backlog", "Open") '
            'AND resolution = Unresolved',
            maxResults=0,
        ).total

        # Закрыто за эту неделю
        resolved_this_week = self.client.search_issues(
            'assignee = currentUser() AND resolved >= startOfWeek()',
            maxResults=0,
        ).total

        # Всего открытых задач
        total_assigned = self.client.search_issues(
            'assignee = currentUser() AND resolution = Unresolved',
            maxResults=0,
        ).total

        return JiraStats(
            in_progress=in_progress,
            in_backlog=in_backlog,
            resolved_this_week=resolved_this_week,
            total_assigned=total_assigned,
        )

    def _search_issues(self, jql: str, include_assignee: bool = False) -> list[JiraTask]:
        """Выполняет поиск задач и возвращает список объектов JiraTask."""
        fields = ["key", "summary", "status"]
        if include_assignee:
            fields.append("assignee")

        issues = self.client.search_issues(
            jql,
            fields=fields,
            maxResults=self.MAX_RESULTS,
        )

        return [
            JiraTask(
                key=issue.key,
                summary=issue.fields.summary,
                url=f"{settings.jira_url}/browse/{issue.key}",
                status=issue.fields.status.name,
                assignee=getattr(issue.fields.assignee, "displayName", None) if include_assignee else None,
            )
            for issue in issues
        ]

    async def get_current_user(self) -> str:
        """Возвращает имя текущего пользователя Jira."""
        return await asyncio.to_thread(self.client.current_user)

    async def get_events_since(self, since: datetime) -> list[JiraEvent]:
        """Получает события по задачам пользователя с указанного времени (асинхронно)."""
        return await asyncio.to_thread(self._get_events_since_sync, since)

    def _get_events_since_sync(self, since: datetime) -> list[JiraEvent]:
        """Получает события по задачам пользователя с указанного времени.

        Отслеживает:
        - Создание новых задач (назначенных на меня)
        - Новые комментарии
        - Изменения статуса
        - Новые назначения на меня

        Для задач где пользователь: assignee, reporter или watcher.
        """
        events: list[JiraEvent] = []
        current_user = self.client.current_user()

        # Формат даты для JQL
        since_str = since.strftime("%Y-%m-%d %H:%M")

        # Поиск задач, обновлённых с указанного времени
        # Ищем задачи где я assignee, reporter или watcher
        jql = (
            f'(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()) '
            f'AND updated >= "{since_str}" ORDER BY updated DESC'
        )

        issues = self.client.search_issues(
            jql,
            fields=["key", "summary", "status", "comment", "assignee", "created", "reporter"],
            maxResults=self.MAX_RESULTS,
            expand="changelog",
        )

        for issue in issues:
            issue_url = f"{settings.jira_url}/browse/{issue.key}"

            # Проверяем, была ли задача создана после since (новая задача)
            created_dt = self._parse_jira_datetime(issue.fields.created)
            if created_dt is not None and created_dt > since:
                # Получаем автора (reporter)
                reporter_name = getattr(issue.fields.reporter, "name", "") or getattr(issue.fields.reporter, "accountId", "")
                reporter_display = getattr(issue.fields.reporter, "displayName", reporter_name)

                # Получаем assignee
                assignee_display = getattr(issue.fields.assignee, "displayName", None) if issue.fields.assignee else None
                details = f"Назначено: {assignee_display}" if assignee_display else "Без исполнителя"

                events.append(JiraEvent(
                    id=f"created_{issue.key}",
                    issue_key=issue.key,
                    issue_summary=issue.fields.summary,
                    issue_url=issue_url,
                    event_type="created",
                    author=reporter_display,
                    author_id=reporter_name,
                    details=details,
                    timestamp=created_dt,
                ))

            # Проверяем комментарии (только для открытых задач)
            is_closed = issue.fields.status.name in ("Done", "Closed", "Resolved")
            if hasattr(issue.fields, "comment") and issue.fields.comment and not is_closed:
                for comment in issue.fields.comment.comments:
                    comment_created = self._parse_jira_datetime(comment.created)
                    # Пропускаем события с некорректной датой или старые
                    if comment_created is None or comment_created <= since:
                        continue

                    author_name = getattr(comment.author, "name", "") or getattr(comment.author, "accountId", "")
                    author_display = getattr(comment.author, "displayName", author_name)

                    # Обрезаем длинные комментарии
                    body = comment.body[:200] + "..." if len(comment.body) > 200 else comment.body

                    events.append(JiraEvent(
                        id=f"comment_{comment.id}",
                        issue_key=issue.key,
                        issue_summary=issue.fields.summary,
                        issue_url=issue_url,
                        event_type="comment",
                        author=author_display,
                        author_id=author_name,
                        details=body,
                        timestamp=comment_created,
                    ))

            # Проверяем changelog на изменения статуса и назначения
            if hasattr(issue, "changelog") and issue.changelog:
                for history in issue.changelog.histories:
                    history_created = self._parse_jira_datetime(history.created)
                    # Пропускаем события с некорректной датой или старые
                    if history_created is None or history_created <= since:
                        continue

                    author_name = getattr(history.author, "name", "") or getattr(history.author, "accountId", "")
                    author_display = getattr(history.author, "displayName", author_name)

                    for item in history.items:
                        if item.field == "status":
                            events.append(JiraEvent(
                                id=f"status_{history.id}_{item.field}",
                                issue_key=issue.key,
                                issue_summary=issue.fields.summary,
                                issue_url=issue_url,
                                event_type="status_change",
                                author=author_display,
                                author_id=author_name,
                                details=f"{item.fromString} → {item.toString}",
                                timestamp=history_created,
                            ))
                        elif item.field == "assignee" and item.to == current_user:
                            # item.to содержит username/accountId, item.toString - displayName
                            events.append(JiraEvent(
                                id=f"assign_{history.id}_{item.field}",
                                issue_key=issue.key,
                                issue_summary=issue.fields.summary,
                                issue_url=issue_url,
                                event_type="assigned",
                                author=author_display,
                                author_id=author_name,
                                details=f"Назначено на вас",
                                timestamp=history_created,
                            ))

        # Сортируем по времени
        events.sort(key=lambda e: e.timestamp)
        return events

    def _parse_jira_datetime(self, dt_str: str) -> datetime | None:
        """Парсит строку даты из Jira API.

        Jira возвращает даты в формате: 2024-01-15T10:30:00.000+0000
        Возвращает datetime в UTC или None при ошибке парсинга.
        """
        try:
            # Убираем миллисекунды, оставляем таймзону
            if "." in dt_str:
                base, rest = dt_str.split(".")
                # rest = "000+0000" или "000-0500"
                tz_part = rest[3:] if len(rest) > 3 else "+0000"
                dt_str = base + tz_part

            # Парсим с таймзоной (формат: 2024-01-15T10:30:00+0000)
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S%z")
            # Конвертируем в UTC для единообразия
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Ошибка парсинга даты '{dt_str}': {e}")
            return None


jira_service = JiraService()
