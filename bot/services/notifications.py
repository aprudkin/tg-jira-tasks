import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service, JiraEvent

logger = logging.getLogger(__name__)

# Путь к файлу состояния (в Docker монтируется через volume)
STATE_FILE = Path("/app/data/sync_state.json")


class NotificationService:
    """Сервис для отправки уведомлений о событиях Jira."""

    # Интервал проверки по умолчанию в минутах
    DEFAULT_INTERVAL_MINUTES = 30

    def __init__(self) -> None:
        self._chat_id: int | None = None
        self._last_check: datetime | None = None
        self._interval_minutes: int = self.DEFAULT_INTERVAL_MINUTES
        self._task: asyncio.Task | None = None
        self._bot: Bot | None = None
        # Загружаем сохранённое состояние при инициализации
        self._load_state()

    def _load_state(self) -> None:
        """Загружает состояние подписки из файла."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                self._chat_id = data.get("chat_id")
                self._interval_minutes = data.get("interval_minutes", self.DEFAULT_INTERVAL_MINUTES)
                # last_check ставим на текущее время, чтобы не слать старые уведомления
                if self._chat_id is not None:
                    self._last_check = datetime.now()
                    logger.info(f"Restored subscription (chat_id={self._chat_id}, interval={self._interval_minutes} min)")
        except Exception as e:
            logger.error(f"Error loading state: {e}")

    def _save_state(self) -> None:
        """Сохраняет состояние подписки в файл."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "chat_id": self._chat_id,
                "interval_minutes": self._interval_minutes,
            }
            STATE_FILE.write_text(json.dumps(data))
            logger.info("Subscription state saved")
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def subscribe(self, chat_id: int, interval_minutes: int | None = None) -> bool:
        """Подписывает на уведомления с указанным интервалом."""
        if self._chat_id is not None:
            return False
        self._chat_id = chat_id
        self._last_check = datetime.now()
        self._interval_minutes = interval_minutes or self.DEFAULT_INTERVAL_MINUTES
        self._save_state()
        logger.info(f"Subscribed to notifications (interval: {self._interval_minutes} min)")
        return True

    def unsubscribe(self, chat_id: int) -> bool:
        """Отписывает от уведомлений."""
        if self._chat_id != chat_id:
            return False
        self._chat_id = None
        self._last_check = None
        self._save_state()
        logger.info("Unsubscribed from notifications")
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        """Проверяет, подписан ли пользователь."""
        return self._chat_id == chat_id

    def get_interval(self) -> int:
        """Возвращает текущий интервал проверки в минутах."""
        return self._interval_minutes

    def start(self, bot: Bot) -> None:
        """Запускает фоновую задачу проверки уведомлений."""
        self._bot = bot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_loop())
            logger.info("Notification service started")

    def stop(self) -> None:
        """Останавливает фоновую задачу."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Notification service stopped")

    async def _check_loop(self) -> None:
        """Цикл проверки уведомлений."""
        while True:
            try:
                # Ждём интервал в секундах
                await asyncio.sleep(self._interval_minutes * 60)
                await self._check_notifications()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in notification loop: {e}")

    async def _check_notifications(self) -> None:
        """Проверяет уведомления."""
        if not self._bot or self._chat_id is None or self._last_check is None:
            return

        try:
            events = await asyncio.to_thread(
                jira_service.get_events_since, self._last_check
            )

            if events:
                await self._send_events(self._chat_id, events)

            # Обновляем время последней проверки
            self._last_check = datetime.now()

        except Exception as e:
            logger.error(f"Error checking events: {e}")

    async def _send_events(self, chat_id: int, events: list[JiraEvent]) -> None:
        """Отправляет уведомления о событиях пользователю."""
        if not self._bot:
            return

        for event in events:
            message = self._format_event(event)
            try:
                await self._bot.send_message(chat_id, message)
                # Небольшая задержка между сообщениями
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending notification to {chat_id}: {e}")

    def _format_event(self, event: JiraEvent) -> str:
        """Форматирует событие для отправки."""
        event_icons = {
            "comment": "💬",
            "status_change": "🔄",
            "assigned": "👤",
        }
        icon = event_icons.get(event.event_type, "📌")

        event_titles = {
            "comment": "Новый комментарий",
            "status_change": "Изменение статуса",
            "assigned": "Назначение",
        }
        title = event_titles.get(event.event_type, "Обновление")

        lines = [
            f"{icon} {hbold(title)}",
            f"{hlink(event.issue_key, event.issue_url)}: {event.issue_summary}",
            f"От: {event.author}",
            f"{event.details}",
        ]

        return "\n".join(lines)


# Глобальный экземпляр сервиса уведомлений
notification_service = NotificationService()
