import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jira import JIRA
from jira.resources import dict2resource

from bot import status
from bot.config import settings

logger = logging.getLogger(__name__)


class IncompleteJiraDataError(RuntimeError):
    """Jira не позволила получить полный результат в безопасных пределах."""


def utc_now_naive() -> datetime:
    """Текущее время в UTC без tzinfo — формат для сравнений с разобранными датами Jira."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _jql_in(field: str, statuses: tuple[str, ...], negate: bool = False) -> str:
    """Собирает JQL-условие вида `field in ("a", "b")` из кортежа статусов (status.*)."""
    values = ", ".join(f'"{s}"' for s in statuses)
    op = "not in" if negate else "in"
    return f"{field} {op} ({values})"


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
    timestamp: datetime = field(default_factory=utc_now_naive)
    # Только для event_type="status_change": имя нового статуса (item.toString)
    to_status: str | None = None


@dataclass
class JiraStats:
    """Статистика по задачам."""

    in_progress: int
    in_backlog: int
    resolved_this_week: int
    total_assigned: int


class JiraService:
    """Сервис для работы с Jira API."""

    # Размер страницы ограничивает один запрос, лимиты — память и число продолжений.
    PAGE_SIZE = 100
    INTERACTIVE_RESULT_LIMIT = 500
    EVENT_ISSUE_LIMIT = 2_000
    HISTORY_LIMIT_PER_ISSUE = 5_000
    HISTORY_LIMIT_PER_POLL = 20_000
    SEARCH_PAGE_LIMIT = 100
    HISTORY_PAGE_LIMIT = 100

    def __init__(self) -> None:
        self._client: JIRA | None = None
        # Lock защищает ленивую инициализацию от гонки между потоками gather()
        self._client_lock = threading.Lock()
        # Проверяем наличие конфигурации при инициализации, но не подключаемся
        if not (settings.jira_pat or (settings.jira_email and settings.jira_api_token)):
            raise ValueError("Jira configuration missing: enable JIRA_PAT or JIRA_EMAIL/JIRA_API_TOKEN")

    @property
    def client(self) -> JIRA:
        """Ленивая инициализация клиента Jira (потокобезопасная)."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                auth = (
                    {"token_auth": settings.jira_pat}
                    if settings.jira_pat
                    else {"basic_auth": (settings.jira_email, settings.jira_api_token)}
                )
                self._client = JIRA(server=settings.jira_url, timeout=30, **auth)
            return self._client

    async def get_my_tasks_in_progress(self) -> list[JiraTask]:
        """Получает задачи текущего пользователя в статусе 'In Progress'."""
        return await asyncio.to_thread(self._get_my_tasks_in_progress_sync)

    def _get_my_tasks_in_progress_sync(self) -> list[JiraTask]:
        jql = f'assignee = currentUser() AND status = "{status.IN_PROGRESS}"'
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
            f'assignee = currentUser() AND {_jql_in("status", status.BACKLOG_GROUP)} '
            'AND resolution = Unresolved ORDER BY priority DESC, created ASC'
        )
        return self._search_issues(jql)

    async def get_waiting_tasks(self) -> list[JiraTask]:
        """Получает задачи в статусах Discussion / Hold."""
        return await asyncio.to_thread(self._get_waiting_tasks_sync)

    def _get_waiting_tasks_sync(self) -> list[JiraTask]:
        jql = (
            f'assignee = currentUser() AND {_jql_in("status", status.WAITING_GROUP)} '
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
            f'AND {_jql_in("status", status.CLOSED_GROUP, negate=True)} '
            'ORDER BY updated DESC'
        )
        return self._search_issues(jql, include_assignee=True)

    async def get_stats(self) -> JiraStats:
        """Получает статистику по задачам параллельно."""
        jqls = (
            f'assignee = currentUser() AND status = "{status.IN_PROGRESS}"',
            f'assignee = currentUser() AND {_jql_in("status", status.BACKLOG_GROUP)} '
            'AND resolution = Unresolved',
            'assignee = currentUser() AND resolved >= startOfWeek()',
            'assignee = currentUser() AND resolution = Unresolved',
        )
        in_progress, in_backlog, resolved_this_week, total_assigned = await asyncio.gather(
            *(asyncio.to_thread(self._count_issues, jql) for jql in jqls)
        )
        return JiraStats(
            in_progress=in_progress,
            in_backlog=in_backlog,
            resolved_this_week=resolved_this_week,
            total_assigned=total_assigned,
        )

    def _count_issues(self, jql: str) -> int:
        """Возвращает количество задач, соответствующих JQL."""
        return self.client.search_issues(jql, maxResults=0).total

    async def count_assigned(self, user: str) -> int:
        """Число задач, назначенных на user — проба видимости для /track.

        Бросает исключение, если Jira не может прочитать (нет прав / нет такого юзера).
        """
        return await asyncio.to_thread(self._count_issues, f'assignee = "{user}"')

    @staticmethod
    def _stable_jql(jql: str) -> str:
        """Добавляет неизменяемый tie-breaker к сортировке между страницами."""
        if "ORDER BY" in jql.upper():
            return f"{jql}, key ASC"
        return f"{jql} ORDER BY key ASC"

    def _search_issue_pages(
        self,
        jql: str,
        *,
        fields: list[str],
        limit: int,
        expand: str | None = None,
    ) -> list:
        """Читает Cloud token-pages или DC offset-pages, не возвращая неполный результат."""
        jql = self._stable_jql(jql)
        issues: list = []
        seen_keys: set[str] = set()
        raw_count = 0

        if getattr(self.client, "_is_cloud", False) is True:
            token: str | None = None
            seen_tokens: set[str] = set()
            page_count = 0
            while True:
                page_count += 1
                if page_count > self.SEARCH_PAGE_LIMIT:
                    raise IncompleteJiraDataError("Jira search exceeded the page request limit")
                page = self.client.enhanced_search_issues(
                    jql,
                    nextPageToken=token,
                    fields=fields,
                    maxResults=self.PAGE_SIZE,
                    expand=expand,
                )
                page_items = list(page)
                raw_count += len(page_items)
                if raw_count > limit:
                    raise IncompleteJiraDataError(f"Jira search exceeded {limit} issues")
                added = 0
                for issue in page_items:
                    if issue.key not in seen_keys:
                        seen_keys.add(issue.key)
                        issues.append(issue)
                        added += 1

                next_token = getattr(page, "nextPageToken", None)
                if not next_token:
                    return issues
                if not page_items or added == 0:
                    raise IncompleteJiraDataError("Jira search page did not add any issues")
                if next_token == token or next_token in seen_tokens:
                    raise IncompleteJiraDataError("Jira search returned a repeated page token")
                seen_tokens.add(next_token)
                token = next_token

        start_at = 0
        page_count = 0
        while True:
            page_count += 1
            if page_count > self.SEARCH_PAGE_LIMIT:
                raise IncompleteJiraDataError("Jira search exceeded the page request limit")
            page = self.client.search_issues(
                jql,
                startAt=start_at,
                fields=fields,
                maxResults=self.PAGE_SIZE,
                expand=expand,
            )
            page_items = list(page)
            response_start = getattr(page, "startAt", start_at)
            if response_start != start_at:
                raise IncompleteJiraDataError("Jira search returned a non-advancing offset")

            raw_count += len(page_items)
            if raw_count > limit:
                raise IncompleteJiraDataError(f"Jira search exceeded {limit} issues")
            added = 0
            for issue in page_items:
                if issue.key not in seen_keys:
                    seen_keys.add(issue.key)
                    issues.append(issue)
                    added += 1

            total = getattr(page, "total", None)
            if not page_items:
                if total is not None and start_at < total:
                    raise IncompleteJiraDataError("Jira search returned an empty page before total")
                return issues
            if getattr(page, "isLast", False) is True:
                return issues
            if added == 0:
                raise IncompleteJiraDataError("Jira search page did not add any issues")

            next_start = start_at + len(page_items)
            response_size = getattr(page, "maxResults", None) or self.PAGE_SIZE
            # ResultList подставляет len(page), когда сервер не прислал total.
            # Поэтому полную страницу всегда подтверждаем ещё одним запросом.
            if len(page_items) < response_size and (total is None or next_start >= total):
                return issues
            if next_start <= start_at:
                raise IncompleteJiraDataError("Jira search offset did not advance")
            start_at = next_start

    def _search_issues(self, jql: str, include_assignee: bool = False) -> list[JiraTask]:
        """Выполняет полный ограниченный поиск задач и возвращает JiraTask."""
        fields = ["key", "summary", "status"]
        if include_assignee:
            fields.append("assignee")

        issues = self._search_issue_pages(
            jql,
            fields=fields,
            limit=self.INTERACTIVE_RESULT_LIMIT,
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

    async def get_events_since(self, since: datetime, target: str | None = None) -> list[JiraEvent]:
        """Получает события по задачам целевого юзера с указанного времени (асинхронно).

        target=None → личный канал (currentUser); иначе — канал коллеги (assignee=target).
        """
        return await asyncio.to_thread(self._get_events_since_sync, since, target)

    def _get_events_since_sync(self, since: datetime, target: str | None = None) -> list[JiraEvent]:
        """Получает события по задачам целевого юзера с указанного времени.

        Отслеживает: создание задач, новые комментарии, изменения статуса, новые назначения.

        target=None → личный канал: задачи где я assignee, reporter или watcher.
        target="X"  → канал коллеги: только задачи, назначенные на X (ADR-0001).
        """
        events: list[JiraEvent] = []

        # Формат даты для JQL
        since_str = since.strftime("%Y-%m-%d %H:%M")

        if target is None:
            # Личный канал: задачи, где я assignee, reporter или watcher
            assign_target = self.client.current_user()
            jql = (
                f'(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()) '
                f'AND updated >= "{since_str}" ORDER BY updated DESC'
            )
        else:
            # Канал коллеги: только назначенные на него (ADR-0001) — это и «его тикеты»
            # по смыслу, и обход прав Manage Watchers (watcher по чужому юзеру не спрашиваем).
            assign_target = target
            jql = f'assignee = "{target}" AND updated >= "{since_str}" ORDER BY updated DESC'

        issues = self._search_issue_pages(
            jql,
            fields=["key", "summary", "status", "comment", "assignee", "created", "reporter"],
            limit=self.EVENT_ISSUE_LIMIT,
            expand="changelog",
        )
        history_count = 0

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
            is_closed = issue.fields.status.name in status.CLOSED_GROUP
            comments = []
            if hasattr(issue.fields, "comment") and issue.fields.comment and not is_closed:
                comments = self._complete_comments(issue)
                history_count += len(comments)
                self._check_poll_history_limit(history_count)
                for comment in comments:
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
                histories = self._complete_changelog(issue)
                history_count += len(histories)
                self._check_poll_history_limit(history_count)
                for history in histories:
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
                                to_status=item.toString,
                            ))
                        elif item.field == "assignee" and item.to == assign_target:
                            # item.to содержит username/accountId, item.toString - displayName
                            assigned_details = (
                                "Назначено на вас" if target is None
                                else f"Назначено на {item.toString}"
                            )
                            events.append(JiraEvent(
                                id=f"assign_{history.id}_{item.field}",
                                issue_key=issue.key,
                                issue_summary=issue.fields.summary,
                                issue_url=issue_url,
                                event_type="assigned",
                                author=author_display,
                                author_id=author_name,
                                details=assigned_details,
                                timestamp=history_created,
                            ))

        # Повтор на границе страниц не должен порождать два события в одном опросе.
        unique_events = {(event.issue_key, event.id): event for event in events}
        return sorted(unique_events.values(), key=lambda event: (event.timestamp, event.issue_key, event.id))

    @staticmethod
    def _embedded_page_is_incomplete(container, items: list) -> bool:
        """Проверяет усечение embedded-коллекции по Jira pagination metadata."""
        total = getattr(container, "total", None)
        start_at = getattr(container, "startAt", 0)
        return total is not None and start_at + len(items) < total

    def _complete_comments(self, issue) -> list:
        """Возвращает полные комментарии либо завершает весь опрос ошибкой."""
        container = issue.fields.comment
        embedded = list(container.comments)
        has_raw = isinstance(getattr(issue, "raw", None), dict)
        incomplete = self._embedded_page_is_incomplete(container, embedded)
        total = getattr(container, "total", None)
        if len(embedded) > self.HISTORY_LIMIT_PER_ISSUE or (
            total is not None and total > self.HISTORY_LIMIT_PER_ISSUE
        ):
            raise IncompleteJiraDataError(
                f"Comments for {issue.key} exceeded {self.HISTORY_LIMIT_PER_ISSUE} records"
            )
        if total is not None and not incomplete:
            return embedded
        if total is None and not has_raw:
            return embedded
        return self._fetch_offset_history(issue.key, "comment", "comments")

    def _complete_changelog(self, issue) -> list:
        """Возвращает полный changelog; DC с доказанным усечением блокирует cursor."""
        container = issue.changelog
        embedded = list(container.histories)
        has_raw = isinstance(getattr(issue, "raw", None), dict)
        incomplete = self._embedded_page_is_incomplete(container, embedded)
        total = getattr(container, "total", None)
        if len(embedded) > self.HISTORY_LIMIT_PER_ISSUE or (
            total is not None and total > self.HISTORY_LIMIT_PER_ISSUE
        ):
            raise IncompleteJiraDataError(
                f"Changelog for {issue.key} exceeded {self.HISTORY_LIMIT_PER_ISSUE} records"
            )
        if total is not None and not incomplete:
            return embedded
        if total is None and not has_raw:
            return embedded

        if getattr(self.client, "_is_cloud", False) is True:
            return self._fetch_offset_history(issue.key, "changelog", "values")
        raise IncompleteJiraDataError(
            f"Changelog for {issue.key} is truncated and Jira Data Center has no supported continuation"
        )

    def _fetch_offset_history(self, issue_key: str, resource: str, items_key: str) -> list:
        """Читает offset-pages history через изолированный raw REST adapter."""
        items: list = []
        seen_ids: set[str] = set()
        start_at = 0
        page_count = 0
        while True:
            page_count += 1
            if page_count > self.HISTORY_PAGE_LIMIT:
                raise IncompleteJiraDataError(
                    f"{resource} for {issue_key} exceeded the page request limit"
                )
            response = self.client._get_json(
                f"issue/{issue_key}/{resource}",
                params={"startAt": start_at, "maxResults": self.PAGE_SIZE},
            )
            if not isinstance(response, dict) or not isinstance(response.get(items_key), list):
                raise IncompleteJiraDataError(f"Malformed {resource} page for {issue_key}")
            response_start = response.get("startAt", start_at)
            if response_start != start_at:
                raise IncompleteJiraDataError(f"Non-advancing {resource} page for {issue_key}")

            raw_items = response[items_key]
            if start_at + len(raw_items) > self.HISTORY_LIMIT_PER_ISSUE:
                raise IncompleteJiraDataError(
                    f"{resource} for {issue_key} exceeded {self.HISTORY_LIMIT_PER_ISSUE} records"
                )
            added = 0
            for raw_item in raw_items:
                item_id = str(raw_item.get("id", ""))
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    items.append(dict2resource(
                        raw_item,
                        options=self.client._options,
                        session=self.client._session,
                    ))
                    added += 1

            next_start = start_at + len(raw_items)
            total = response.get("total")
            page_size = response.get("maxResults", self.PAGE_SIZE)
            if not raw_items:
                if total is not None and start_at < total:
                    raise IncompleteJiraDataError(
                        f"Empty {resource} page before total for {issue_key}"
                    )
                return items
            if total is not None and next_start >= total:
                return items
            if added == 0:
                raise IncompleteJiraDataError(
                    f"{resource} page did not add any records for {issue_key}"
                )
            if total is None and len(raw_items) < page_size:
                return items
            if next_start <= start_at:
                raise IncompleteJiraDataError(f"{resource} offset did not advance for {issue_key}")
            start_at = next_start

    def _check_poll_history_limit(self, count: int) -> None:
        if count > self.HISTORY_LIMIT_PER_POLL:
            raise IncompleteJiraDataError(
                f"Jira history exceeded {self.HISTORY_LIMIT_PER_POLL} records in one poll"
            )

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
