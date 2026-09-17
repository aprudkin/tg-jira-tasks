"""Small regression checks for the README's code-backed reference tables and links."""

import re
from pathlib import Path
from urllib.parse import urlparse

from bot.config import Settings
from bot.main import BOT_COMMANDS

ROOT = Path(__file__).parents[1]


def _section(document: str, heading: str) -> str:
    remainder = document.split(heading, 1)[1]
    return remainder.split("\n## ", 1)[0]


def _first_column_code(section: str) -> set[str]:
    return {
        match.group(1)
        for line in section.splitlines()
        if (match := re.match(r"\| `([^`]+)` \|", line))
    }


def test_readme_command_table_matches_registered_bot_commands():
    readme = (ROOT / "README.md").read_text()
    documented = {
        cell.split()[0].removeprefix("/")
        for cell in _first_column_code(_section(readme, "## Команды бота"))
    }
    registered = {command.command for command in BOT_COMMANDS}

    assert documented == registered


def test_readme_configuration_table_matches_settings():
    readme = (ROOT / "README.md").read_text()
    documented = _first_column_code(_section(readme, "## Конфигурация"))
    configured = {name.upper() for name in Settings.model_fields}

    assert documented == configured


def test_documentation_local_links_resolve():
    missing: list[str] = []
    for document_path in (ROOT / "README.md", ROOT / "ARCHITECTURE.md"):
        document = document_path.read_text()
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document):
            parsed = urlparse(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            linked_path = (document_path.parent / parsed.path).resolve()
            if not linked_path.exists():
                missing.append(f"{document_path.name}: {target}")

    assert missing == []
