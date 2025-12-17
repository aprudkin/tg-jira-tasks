import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.utils.markdown import hbold, hlink

from bot.services.jira import jira_service, JiraEvent

logger = logging.getLogger(__name__)


class NotificationService:
    """Сервис для отправки уведомлений о событиях Jira."""

    # Интервал проверки по умолчанию в минутах
    DEFAULT_INTERVAL_MINUTES = 30
    # Минимальный интервал проверки цикла в секундах
    MIN_CHECK_INTERVAL = 60

    def __init__(self) -> None:
        # Словарь подписок: chat_id -> (datetime последней проверки, интервал в минутах)
        self._subscriptions: dict[int, tuple[datetime, int]] = {}
        self._task: asyncio.Task | None = None
        self._bot: Bot | None = None

    def subscribe(self, chat_id: int, interval_minutes: int | None = None) -> bool:
        """Подписывает пользователя на уведомления с указанным интервалом."""
        if chat_id in self._subscriptions:
            return False
        interval = interval_minutes or self.DEFAULT_INTERVAL_MINUTES
        self._subscriptions[chat_id] = (datetime.now(), interval)
        logger.info(f"User {chat_id} subscribed to notifications (interval: {interval} min)")
        return True

    def unsubscribe(self, chat_id: int) -> bool:
        """Отписывает пользователя от уведомлений."""
        if chat_id not in self._subscriptions:
            return False
        del self._subscriptions[chat_id]
        logger.info(f"User {chat_id} unsubscribed from notifications")
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        """Проверяет, подписан ли пользователь."""
        return chat_id in self._subscriptions

    def get_interval(self, chat_id: int) -> int | None:
        """Возвращает интервал проверки для пользователя в минутах."""
        if chat_id in self._subscriptions:
            return self._subscriptions[chat_id][1]
        return None

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
                await asyncio.sleep(self.MIN_CHECK_INTERVAL)
                await self._check_all_subscriptions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in notification loop: {e}")

    async def _check_all_subscriptions(self) -> None:
        """Проверяет уведомления для всех подписчиков."""
        if not self._bot or not self._subscriptions:
            return

        now = datetime.now()

        # Копируем для безопасной итерации
        subscriptions = dict(self._subscriptions)

        for chat_id, (last_check, interval_minutes) in subscriptions.items():
            try:
                # Проверяем, прошёл ли интервал с момента последней проверки
                elapsed_minutes = (now - last_check).total_seconds() / 60
                if elapsed_minutes < interval_minutes:
                    continue

                events = await asyncio.to_thread(
                    jira_service.get_events_since, last_check
                )

                if events:
                    await self._send_events(chat_id, events)

                # Обновляем время последней проверки, сохраняя интервал
                self._subscriptions[chat_id] = (datetime.now(), interval_minutes)

            except Exception as e:
                logger.error(f"Error checking events for {chat_id}: {e}")

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
