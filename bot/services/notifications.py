import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.utils.markdown import hbold, hlink

from bot.config import settings
from bot.services.jira import jira_service, JiraEvent, utc_now_naive, CLOSED_STATUSES

logger = logging.getLogger(__name__)

# Путь к файлу состояния (в Docker монтируется через volume).
# Используется через свойство, чтобы тесты могли подменять settings.state_file.
def _state_file() -> Path:
    return settings.state_file

# Задержка между уведомлениями, чтобы не упереться в rate-limit Telegram
SEND_DELAY_SECONDS = 0.5

# Иконки и заголовки событий (модульный уровень — не пересоздаём dict на каждое событие)
EVENT_ICONS = {
    "created": "🆕",
    "comment": "💬",
    "status_change": "🔄",
    "assigned": "👤",
}
EVENT_TITLES = {
    "created": "Новая задача",
    "comment": "Новый комментарий",
    "status_change": "Изменение статуса",
    "assigned": "Назначение",
}


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
        # Словарь для хранения ID обработанных событий по задачам
        # {issue_key: set(event_ids)}
        self._processed_events: dict[str, set[str]] = {}
        # Множество пользователей, от которых уведомления приходят без звука
        self._silent_users: set[str] = set()
        # Загружаем сохранённое состояние при инициализации
        self._load_state()

    def _load_state(self) -> None:
        """Загружает состояние подписки из файла."""
        try:
            state_file = _state_file()
            if state_file.exists():
                data = json.loads(state_file.read_text())
                self._chat_id = data.get("chat_id")
                self._interval_minutes = data.get("interval_minutes", self.DEFAULT_INTERVAL_MINUTES)
                # Загружаем ID уже отправленных событий
                # Поддержка миграции со старого формата (список)
                raw_processed = data.get("processed_events", data.get("processed_ids", []))

                if isinstance(raw_processed, list):
                    # Старый формат: просто список ID, без привязки к задачам
                    # Очищаем, так как не можем привязать к задачам
                    self._processed_events = {}
                elif isinstance(raw_processed, dict):
                    # Новый формат: {issue_key: [id1, id2]}
                    self._processed_events = {
                        k: set(v) for k, v in raw_processed.items()
                    }
                else:
                    self._processed_events = {}

                # Загружаем silent_users
                self._silent_users = set(data.get("silent_users", []))

                # last_check ставим на текущее время, чтобы не слать старые уведомления
                if self._chat_id is not None:
                    self._last_check = utc_now_naive()
                    logger.info(f"Restored subscription (chat_id={self._chat_id}, interval={self._interval_minutes} min)")
        except Exception:
            logger.exception("Error loading state")

    async def _save_state(self) -> None:
        """Сохраняет состояние подписки в файл."""
        await asyncio.to_thread(self._save_state_sync)

    def _save_state_sync(self) -> None:
        try:
            state_file = _state_file()
            state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "chat_id": self._chat_id,
                "interval_minutes": self._interval_minutes,
                # Преобразуем сеты в списки для JSON
                "processed_events": {
                    k: list(v)[-50:] for k, v in self._processed_events.items()
                },
                "silent_users": list(self._silent_users),
            }
            # Пишем во временный файл и атомарно подменяем — иначе падение
            # бота посреди write_text оставит обрезанный JSON, а _load_state
            # молча сбросит дедуп и подписку.
            tmp = state_file.with_suffix(state_file.suffix + ".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(state_file)
            logger.info("Subscription state saved")
        except Exception:
            logger.exception("Error saving state")

    async def subscribe(self, chat_id: int, interval_minutes: int | None = None) -> bool:
        """Подписывает на уведомления с указанным интервалом."""
        if self._chat_id is not None:
            return False
        self._chat_id = chat_id
        self._last_check = utc_now_naive()
        self._interval_minutes = interval_minutes or self.DEFAULT_INTERVAL_MINUTES
        await self._save_state()
        logger.info(f"Subscribed to notifications (interval: {self._interval_minutes} min)")
        return True

    async def unsubscribe(self, chat_id: int) -> bool:
        """Отписывает от уведомлений."""
        if self._chat_id != chat_id:
            return False
        self._chat_id = None
        self._last_check = None
        await self._save_state()
        logger.info("Unsubscribed from notifications")
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        """Проверяет, подписан ли пользователь."""
        return self._chat_id == chat_id

    def get_interval(self) -> int:
        """Возвращает текущий интервал проверки в минутах."""
        return self._interval_minutes

    async def update_interval(self, chat_id: int, interval_minutes: int) -> bool:
        """Обновляет интервал проверки для подписанного пользователя."""
        if self._chat_id != chat_id:
            return False
        self._interval_minutes = interval_minutes
        await self._save_state()
        logger.info(f"Updated notification interval to {interval_minutes} min")
        return True

    async def check_now(self) -> None:
        """Запускает немедленную проверку уведомлений."""
        await self._check_notifications()

    async def mute_user(self, username: str) -> None:
        """Добавляет пользователя в список тихих."""
        self._silent_users.add(username)
        await self._save_state()

    async def unmute_user(self, username: str) -> None:
        """Убирает пользователя из списка тихих."""
        self._silent_users.discard(username)
        await self._save_state()

    def is_user_silent(self, username: str) -> bool:
        """Проверяет, находится ли пользователь в списке тихих."""
        return username in self._silent_users

    def get_silent_users(self) -> set[str]:
        """Возвращает список тихих пользователей."""
        return self._silent_users

    def start(self, bot: Bot) -> None:
        """Запускает фоновую задачу проверки уведомлений."""
        self._bot = bot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_loop())
            logger.info("Notification service started")

    async def stop(self) -> None:
        """Останавливает фоновую задачу и ожидает её завершения."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Notification service stopped")

    async def _check_loop(self) -> None:
        """Цикл проверки уведомлений."""
        # Первая проверка выполняется сразу после подписки
        first_check = True
        while True:
            try:
                if first_check:
                    # Небольшая задержка для завершения инициализации
                    await asyncio.sleep(5)
                    first_check = False
                else:
                    # Ждём интервал в секундах
                    await asyncio.sleep(self._interval_minutes * 60)
                await self._check_notifications()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in notification loop")

    async def _check_notifications(self) -> None:
        """Проверяет уведомления."""
        if not self._bot or self._chat_id is None or self._last_check is None:
            return

        try:
            events = await jira_service.get_events_since(self._last_check)

            if events:
                # Фильтруем события, которые уже были отправлены
                new_events = []
                for event in events:
                    # Если ключ задачи еще неизвестен, создаем запись
                    if event.issue_key not in self._processed_events:
                        self._processed_events[event.issue_key] = set()

                    # Проверяем, было ли событие уже обработано
                    if event.id not in self._processed_events[event.issue_key]:
                        new_events.append(event)

                if new_events:
                    await self._send_events(self._chat_id, new_events)

                    # Обновляем состояние после отправки
                    for event in new_events:
                        self._processed_events[event.issue_key].add(event.id)

                    # Очищаем состояние для задач, перешедших в закрытый статус
                    for event in new_events:
                        if event.event_type == "status_change" and event.to_status in CLOSED_STATUSES:
                            self._processed_events.pop(event.issue_key, None)

                    # Сохраняем состояние (обновленный список ID)
                    await self._save_state()

            # Обновляем время последней проверки
            self._last_check = utc_now_naive()

        except Exception:
            logger.exception("Error checking events")

    async def _send_events(self, chat_id: int, events: list[JiraEvent]) -> None:
        """Отправляет уведомления о событиях пользователю."""
        if not self._bot:
            return

        for i, event in enumerate(events):
            if i > 0:
                await asyncio.sleep(SEND_DELAY_SECONDS)
            await self._send_one(chat_id, event)

    async def _send_one(self, chat_id: int, event: JiraEvent) -> None:
        """Отправляет одно событие с retry на TelegramRetryAfter (HTTP 429)."""
        for attempt in range(2):
            try:
                await self._bot.send_message(
                    chat_id,
                    self._format_event(event),
                    disable_notification=event.author_id in self._silent_users,
                )
                return
            except TelegramRetryAfter as e:
                if attempt == 0:
                    logger.warning(f"Telegram rate-limit, sleeping {e.retry_after}s")
                    await asyncio.sleep(e.retry_after)
                    continue
                logger.error(f"Rate-limited twice for chat {chat_id}, dropping event {event.id}")
            except Exception:
                logger.exception(f"Error sending notification to {chat_id}")
                return

    def _format_event(self, event: JiraEvent) -> str:
        """Форматирует событие для отправки."""
        icon = EVENT_ICONS.get(event.event_type, "📌")
        title = EVENT_TITLES.get(event.event_type, "Обновление")
        lines = [
            f"{icon} {hbold(title)}",
            f"{hlink(event.issue_key, event.issue_url)}: {event.issue_summary}",
            f"От: {event.author}",
            f"{event.details}",
        ]
        return "\n".join(lines)


# Глобальный экземпляр сервиса уведомлений
notification_service = NotificationService()
