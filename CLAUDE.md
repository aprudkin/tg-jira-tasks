# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Core Instructions

### Investigate Before Answering

Never speculate about code you have not opened. If the user refers to a specific file, you MUST read that file before answering. Always investigate and read all relevant files BEFORE answering questions about the codebase. Never make assertions about code without investigation; if unsure about the correct answer, provide well-founded responses and clearly state any uncertainty instead of hallucinating.

### Do Not Act Before Instructions

Never modify or create code, files, commits, configuration, or tests unless the user has explicitly requested it. If instructions are ambiguous or allow multiple interpretations, ALWAYS ask clarifying questions before taking action.

### Use Context7

Always use context7 when I need code generation, setup or configuration steps, or library/API documentation. This means you should automatically use the Context7 MCP tools to resolve library id and get library docs without me having to explicitly ask.

### Comments in Russian

All comments in the code must be written in Russian.

### PowerShell File Encoding

Encoding for all new `.ps1` files must be UTF-8 with BOM (utf8bom).

### Commit After Changes

After all changes, create a commit following Conventional Commits rules.

## Build and Run Commands

```bash
# Build Docker image
docker build -t tg-jira-bot .

# Run with environment file
docker run --env-file .env tg-jira-bot

# Run with docker-compose
docker-compose up --build
```

## Architecture

Telegram bot (aiogram 3.x) that integrates with Jira to fetch user's tasks.

**Key components:**
- `bot/main.py` - Entry point, configures Dispatcher with middleware and routers
- `bot/config.py` - Settings via pydantic-settings, loads from environment variables
- `bot/services/jira.py` - JiraService class wrapping jira-python library, uses JQL queries
- `bot/handlers/tasks.py` - Command handlers (Router pattern from aiogram)
- `bot/middlewares/auth.py` - Telegram user whitelist middleware

**Flow:** User command → AuthMiddleware (whitelist check) → Handler → JiraService → Response

## Environment Variables

Required in `.env` (see `.env.example`):
- `TELEGRAM_TOKEN` - Bot token from @BotFather
- `JIRA_URL` - Jira server URL
- `JIRA_EMAIL` - Jira account email (Optional if PAT used)
- `JIRA_API_TOKEN` - Jira API token (Optional if PAT used)
- `JIRA_PAT` - Jira Personal Access Token (For Data Center/Server)
- `ALLOWED_USERS` - Comma-separated Telegram user IDs (empty = allow all)
