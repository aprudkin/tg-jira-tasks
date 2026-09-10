"""Регрессии ленивой инициализации Jira-клиента вне event loop."""

import asyncio
import threading

import pytest

import bot.services.jira as jira_module
from bot.services.jira import JiraService


@pytest.mark.asyncio
async def test_get_current_user_initializes_client_without_blocking_event_loop(monkeypatch):
    """Медленный конструктор и current_user должны выполняться в рабочем потоке."""
    event_loop_thread = threading.get_ident()
    constructor_started = threading.Event()
    allow_constructor_to_finish = threading.Event()
    call_threads: dict[str, int] = {}

    class FakeClient:
        def current_user(self) -> str:
            call_threads["current_user"] = threading.get_ident()
            return "jdoe"

    def slow_constructor(**kwargs):
        call_threads["constructor"] = threading.get_ident()
        constructor_started.set()
        if not allow_constructor_to_finish.wait(timeout=2):
            raise TimeoutError("event loop did not release Jira constructor")
        return FakeClient()

    monkeypatch.setattr(jira_module, "JIRA", slow_constructor)
    service = JiraService()

    request = asyncio.create_task(service.get_current_user())
    try:
        async def wait_for_constructor() -> None:
            while not constructor_started.is_set():
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_constructor(), timeout=0.5)
    finally:
        allow_constructor_to_finish.set()

    assert await request == "jdoe"
    assert call_threads["constructor"] != event_loop_thread
    assert call_threads["current_user"] == call_threads["constructor"]


@pytest.mark.asyncio
async def test_concurrent_cold_start_creates_single_client(monkeypatch):
    """Параллельные первые запросы должны разделять один Jira-клиент."""
    constructor_calls = 0
    counter_lock = threading.Lock()

    class FakeClient:
        def current_user(self) -> str:
            return "jdoe"

    def constructor(**kwargs):
        nonlocal constructor_calls
        with counter_lock:
            constructor_calls += 1
        return FakeClient()

    monkeypatch.setattr(jira_module, "JIRA", constructor)
    service = JiraService()

    users = await asyncio.gather(*(service.get_current_user() for _ in range(10)))

    assert users == ["jdoe"] * 10
    assert constructor_calls == 1
