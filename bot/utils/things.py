"""Утилиты для генерации Things URL.

Things — приложение для управления задачами на macOS/iOS.
Использует URL-схему things:// для интеграции.
Документация: https://culturedcode.com/things/support/articles/2803573/
"""

import base64
import json
from urllib.parse import quote, urlencode

from bot.services.jira import JiraTask


def generate_things_add_url(task: JiraTask) -> str:
    """Генерирует URL для добавления одной задачи в Things.

    Args:
        task: Задача из Jira

    Returns:
        URL вида things:///add?title=...&notes=...
    """
    params = {
        "title": f"[{task.key}] {task.summary}",
        "notes": f"Jira: {task.url}",
        "when": "anytime",
        "tags": "jira",
    }
    return f"things:///add?{urlencode(params, quote_via=quote)}"


def generate_things_json_url(
    tasks: list[JiraTask],
    project_name: str | None = None,
) -> str:
    """Генерирует URL для массового импорта задач через JSON.

    Args:
        tasks: Список задач из Jira
        project_name: Если указано, задачи будут обёрнуты в проект Things

    Returns:
        URL вида things:///json?data=...
    """
    items = [
        {
            "type": "to-do",
            "attributes": {
                "title": f"[{task.key}] {task.summary}",
                "notes": f"Jira: {task.url}",
                "tags": ["jira"],
                "when": "anytime",
            },
        }
        for task in tasks
    ]

    if project_name:
        # Обернуть в проект
        data = [
            {
                "type": "project",
                "attributes": {"title": project_name},
                "items": items,
            }
        ]
    else:
        data = items

    json_data = json.dumps(data)
    encoded = base64.urlsafe_b64encode(json_data.encode()).decode()
    return f"things:///json?data={encoded}"
