"""Единый владелец словаря статусов Jira.

Канонические имена, порядок отображения и семантические группы статусов живут здесь
и только здесь. Чистый доменный модуль: без знания о JQL и без внешних зависимостей —
JQL из групп собирает jira.py, отображение по порядку — handlers.
"""

# Канонические имена статусов Jira (как их возвращает issue.fields.status.name).
IN_PROGRESS = "In Progress"
REOPENED = "Reopened"
DISCUSSION = "Discussion"
ON_HOLD = "On Hold"
TO_DO = "To Do"
BACKLOG = "Backlog"
OPEN = "Open"
RESOLVED = "Resolved"
DONE = "Done"
CLOSED = "Closed"

# Канонический порядок отображения при группировке по статусу.
# Статусы вне этого списка сортируются последними (см. render_grouped_by_status).
ORDER = (
    IN_PROGRESS,
    REOPENED,
    DISCUSSION,
    ON_HOLD,
    TO_DO,
    BACKLOG,
    OPEN,
    RESOLVED,
    DONE,
    CLOSED,
)

# Семантические группы — несколько статусов Jira, которые трактуются одинаково.
# Кортежи (а не frozenset): порядок детерминирован, чтобы сборка JQL была стабильной.
BACKLOG_GROUP = (TO_DO, BACKLOG, OPEN)      # не начатые
WAITING_GROUP = (DISCUSSION, ON_HOLD)       # ждут решения
CLOSED_GROUP = (DONE, CLOSED, RESOLVED)     # завершённые (после перехода можно чистить историю событий)
