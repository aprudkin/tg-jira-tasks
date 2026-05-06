"""Тесты не должны требовать реальные секреты — подкладываем dummy env до импорта bot."""
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("JIRA_URL", "http://jira.test")
os.environ.setdefault("JIRA_PAT", "test-pat")
