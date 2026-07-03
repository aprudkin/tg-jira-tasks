import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.utils.markdown import hbold, hlink

from bot.config import settings
from bot.services.jira import jira_service, JiraEvent, utc_now_naive
from bot.status import CLOSED_GROUP

logger = logging.getLogger(__name__)

# Путь к файлу состояния (в Docker монтируется через volume).
# Используется через свойство, чтобы тесты могли подменять settings.state_file.
def _state_file() -> Path:
    return settings.state_file

# Задержка между уведомлениями, чтобы не упереться в rate-limit Telegram
SEND_DELAY_SECONDS = 0.5

# Сентинел-ключ личного канала (currentUser). Не может совпасть с Jira-username.
PERSONAL = "__me__"

# Первая проверка канала — через это число секунд после старта (не ждём полный интервал)
FIRST_CHECK_DELAY_SECONDS = 5

# Палитра маркеров для авто-назначения каналам коллег без явного эмодзи
MARKER_PALETTE = ["🔵", "🟢", "🟣", "🟠", "🔴", "🟡", "🟤", "⚫", "⚪"]

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


@dataclass
class Channel:
    """Независимый канал слежения за одним Jira-юзером (ADR-0001).

    Свой интервал, свой last_check, свой маркер и свой дедуп (ADR-0002).
    """

    user: str  # Jira-username, или PERSONAL для личного канала
    interval_minutes: int
    emoji: str | None = None  # маркер; None у личного канала
    # Дедуп на канал: {issue_key: set(event_ids)}
    processed_events: dict[str, set[str]] = field(default_factory=dict)
    last_check: datetime | None = None

    @property
    def is_personal(self) -> bool:
        return self.user == PERSONAL

    @property
    def jira_target(self) -> str | None:
        """target для jira_service.get_events_since: None у личного канала."""
        return None if self.is_personal else self.user


class NotificationService:
    """Сервис отправки уведомлений о событиях Jira по независимым каналам."""

    # Интервал проверки по умолчанию в минутах
    DEFAULT_INTERVAL_MINUTES = 30

    def __init__(self) -> None:
        self._chat_id: int | None = None
        # Каналы слежения: {user: Channel}; личный канал под ключом PERSONAL
        self._channels: dict[str, Channel] = {}
        # Множество пользователей, от которых уведомления приходят без звука (сквозное, не по каналу)
        self._silent_users: set[str] = set()
        self._bot: Bot | None = None
        # Флаг намеренной остановки — отличает stop() от внешней отмены задачи
        self._stopping: bool = False
        # Фоновые задачи по каналам: {user: Task}
        self._tasks: dict[str, asyncio.Task] = {}
        # Сериализует конкурентные сохранения от разных каналов (общий tmp-файл)
        self._save_lock = asyncio.Lock()
        # Загружаем сохранённое состояние при инициализации
        self._load_state()

    # ---- Состояние -------------------------------------------------------

    def _load_state(self) -> None:
        """Загружает состояние из файла (новая схема или миграция плоской старой)."""
        try:
            state_file = _state_file()
            if not state_file.exists():
                return
            data = json.loads(state_file.read_text())
            self._chat_id = data.get("chat_id")
            self._silent_users = set(data.get("silent_users", []))

            if "channels" in data:
                # Новая многоканальная схема
                for user, ch in data["channels"].items():
                    self._channels[user] = Channel(
                        user=user,
                        interval_minutes=ch.get("interval_minutes", self.DEFAULT_INTERVAL_MINUTES),
                        emoji=ch.get("emoji"),
                        processed_events={k: set(v) for k, v in ch.get("processed_events", {}).items()},
                    )
            elif self._chat_id is not None:
                # Миграция плоской схемы → личный канал __me__
                raw = data.get("processed_events", data.get("processed_ids", []))
                processed = {k: set(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
                self._channels[PERSONAL] = Channel(
                    user=PERSONAL,
                    interval_minutes=data.get("interval_minutes", self.DEFAULT_INTERVAL_MINUTES),
                    emoji=None,
                    processed_events=processed,
                )

            # last_check каждого канала ставим на «сейчас», чтобы не переигрывать старые события
            if self._chat_id is not None:
                now = utc_now_naive()
                for channel in self._channels.values():
                    channel.last_check = now
                logger.info("Restored %d channel(s) (chat_id=%s)", len(self._channels), self._chat_id)
        except Exception:
            logger.exception("Error loading state")

    def _serialize_state(self) -> str:
        """Строит JSON-строку состояния. Вызывается в event-loop потоке (без await),
        поэтому dict'ы каналов не мутируются конкурентно — снапшот атомарен."""
        data = {
            "chat_id": self._chat_id,
            "channels": {
                user: {
                    "interval_minutes": ch.interval_minutes,
                    "emoji": ch.emoji,
                    # Преобразуем сеты в списки, ограничивая историю дедупа
                    "processed_events": {k: list(v)[-50:] for k, v in ch.processed_events.items()},
                }
                for user, ch in self._channels.items()
            },
            "silent_users": list(self._silent_users),
        }
        return json.dumps(data)

    def _write_state(self, payload: str) -> None:
        """Атомарно пишет уже сериализованную строку (может выполняться в to_thread)."""
        try:
            state_file = _state_file()
            state_file.parent.mkdir(parents=True, exist_ok=True)
            # Пишем во временный файл и атомарно подменяем — иначе падение бота посреди
            # write_text оставит обрезанный JSON, а _load_state молча сбросит подписку.
            tmp = state_file.with_suffix(state_file.suffix + ".tmp")
            tmp.write_text(payload)
            tmp.replace(state_file)
            logger.info("Subscription state saved")
        except Exception:
            logger.exception("Error saving state")

    def _save_state_sync(self) -> None:
        """Синхронное сохранение (тесты / прямые вызовы)."""
        try:
            payload = self._serialize_state()
        except Exception:
            logger.exception("Error serializing state")
            return
        self._write_state(payload)

    async def _save_state(self) -> None:
        """Concurrency-safe сохранение: снапшот строится в event-loop потоке, запись —
        под asyncio.Lock, чтобы конкурентные сохранения от разных каналов не гонялись
        за общий tmp-файл (иначе битый JSON → потеря подписки при загрузке)."""
        try:
            payload = self._serialize_state()
        except Exception:
            logger.exception("Error serializing state")
            return
        async with self._save_lock:
            await asyncio.to_thread(self._write_state, payload)

    # ---- Привязка чата и каналы ------------------------------------------

    def _bind_chat(self, chat_id: int) -> bool:
        """Привязывает единственный чат доставки. False — если чат уже другой."""
        if self._chat_id is None:
            self._chat_id = chat_id
            return True
        return self._chat_id == chat_id

    def bind_chat(self, chat_id: int) -> bool:
        return self._bind_chat(chat_id)

    def _resolve_marker(self, emoji: str | None) -> str | None:
        """Возвращает маркер: явный или следующий свободный из палитры."""
        if emoji is not None:
            return emoji
        used = {ch.emoji for ch in self._channels.values() if ch.emoji}
        for marker in MARKER_PALETTE:
            if marker not in used:
                return marker
        return MARKER_PALETTE[0]  # палитра исчерпана — переиспользуем первый

    async def add_channel(self, user: str, emoji: str | None = None, interval: int | None = None) -> Channel:
        """Создаёт или (идемпотентно) обновляет канал коллеги. Чат должен быть уже привязан."""
        existing = self._channels.get(user)
        if existing is not None:
            existing.interval_minutes = interval or existing.interval_minutes
            if emoji is not None:
                existing.emoji = emoji
            existing.last_check = utc_now_naive()
            channel = existing
        else:
            channel = Channel(
                user=user,
                interval_minutes=interval or self.DEFAULT_INTERVAL_MINUTES,
                emoji=self._resolve_marker(emoji),
                last_check=utc_now_naive(),
            )
            self._channels[user] = channel
            self._start_channel_task(channel)
        await self._save_state()
        return channel

    async def remove_channel(self, user: str) -> bool:
        """Убирает канал коллеги (личный канал через remove_channel не трогается)."""
        if user == PERSONAL or user not in self._channels:
            return False
        await self._remove_channel_internal(user)
        return True

    async def _remove_channel_internal(self, user: str) -> None:
        self._channels.pop(user, None)
        self._cancel_channel_task(user)
        if not self._channels:
            self._chat_id = None
        await self._save_state()

    def list_channels(self) -> list[Channel]:
        """Каналы: личный первым, коллеги по имени."""
        return sorted(self._channels.values(), key=lambda c: (not c.is_personal, c.user))

    def get_channel(self, user: str) -> Channel | None:
        return self._channels.get(user)

    # ---- Личный канал (/sync, /unsync) -----------------------------------

    async def subscribe(self, chat_id: int, interval_minutes: int | None = None) -> bool:
        """Подписывает личный канал. False — чат занят другим или уже подписан."""
        if not self._bind_chat(chat_id):
            return False
        if PERSONAL in self._channels:
            return False
        channel = Channel(
            user=PERSONAL,
            interval_minutes=interval_minutes or self.DEFAULT_INTERVAL_MINUTES,
            emoji=None,
            last_check=utc_now_naive(),
        )
        self._channels[PERSONAL] = channel
        self._start_channel_task(channel)
        await self._save_state()
        logger.info("Subscribed personal channel (interval: %d min)", channel.interval_minutes)
        return True

    async def unsubscribe(self, chat_id: int) -> bool:
        """Отписывает личный канал (каналы коллег остаются)."""
        if self._chat_id != chat_id or PERSONAL not in self._channels:
            return False
        await self._remove_channel_internal(PERSONAL)
        logger.info("Unsubscribed personal channel")
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        """Подписан ли личный канал в этом чате."""
        return self._chat_id == chat_id and PERSONAL in self._channels

    def get_interval(self) -> int:
        """Интервал личного канала."""
        channel = self._channels.get(PERSONAL)
        return channel.interval_minutes if channel else self.DEFAULT_INTERVAL_MINUTES

    async def update_interval(self, chat_id: int, interval_minutes: int) -> bool:
        """Обновляет интервал личного канала."""
        channel = self._channels.get(PERSONAL)
        if self._chat_id != chat_id or channel is None:
            return False
        channel.interval_minutes = interval_minutes
        await self._save_state()
        logger.info("Updated personal interval to %d min", interval_minutes)
        return True

    async def check_now(self, user: str = PERSONAL) -> None:
        """Немедленная проверка канала (по умолчанию — личного)."""
        channel = self._channels.get(user)
        if channel is not None:
            await self._check_channel(channel)

    # ---- Тихий режим (сквозной, по автору события) -----------------------

    async def mute_user(self, username: str) -> None:
        self._silent_users.add(username)
        await self._save_state()

    async def unmute_user(self, username: str) -> None:
        self._silent_users.discard(username)
        await self._save_state()

    def is_user_silent(self, username: str) -> bool:
        return username in self._silent_users

    def get_silent_users(self) -> set[str]:
        return self._silent_users

    # ---- Фоновые задачи по каналам ---------------------------------------

    def start(self, bot: Bot) -> None:
        """Запускает фоновые задачи по всем каналам."""
        self._bot = bot
        for channel in self._channels.values():
            self._start_channel_task(channel)
        logger.info("Notification service started (%d channels)", len(self._channels))

    def _start_channel_task(self, channel: Channel) -> None:
        if self._bot is None:
            return
        task = self._tasks.get(channel.user)
        if task is not None and not task.done():
            return
        self._tasks[channel.user] = asyncio.create_task(self._channel_loop(channel))

    def _cancel_channel_task(self, user: str) -> None:
        task = self._tasks.pop(user, None)
        if task is not None and not task.done():
            task.cancel()

    async def stop(self) -> None:
        """Останавливает все фоновые задачи и ожидает их завершения."""
        self._stopping = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Notification service stopped")

    async def _channel_loop(self, channel: Channel) -> None:
        """Цикл проверки одного канала с защитой от неожиданной отмены."""
        sleep_secs = FIRST_CHECK_DELAY_SECONDS  # первая проверка через 5 секунд после старта
        last_heartbeat: float = 0.0
        while True:
            try:
                await asyncio.sleep(sleep_secs)
                sleep_secs = channel.interval_minutes * 60  # интервал читаем динамически

                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= 3600:
                    logger.info("Channel %s alive (interval=%dm)", channel.user, channel.interval_minutes)
                    last_heartbeat = now

                await self._check_channel(channel)
            except asyncio.CancelledError:
                if self._stopping or channel.user not in self._channels:
                    break
                # Неожиданная внешняя отмена — подавляем и перезапускаем цикл
                asyncio.current_task().uncancel()
                logger.warning("Channel %s loop cancelled unexpectedly, restarting", channel.user)
                sleep_secs = FIRST_CHECK_DELAY_SECONDS
            except Exception:
                logger.exception("Error in channel %s loop", channel.user)

    # ---- Проверка и отправка ---------------------------------------------

    async def _check_channel(self, channel: Channel) -> None:
        """Проверяет один канал: дедуп на канал (ADR-0002), маркер канала в уведомлении."""
        if not self._bot or self._chat_id is None or channel.last_check is None:
            return

        try:
            events = await jira_service.get_events_since(channel.last_check, channel.jira_target)

            if events:
                new_events = []
                for event in events:
                    bucket = channel.processed_events.setdefault(event.issue_key, set())
                    if event.id not in bucket:
                        new_events.append(event)

                if new_events:
                    await self._send_events(self._chat_id, new_events, channel.emoji)

                    for event in new_events:
                        channel.processed_events[event.issue_key].add(event.id)

                    # Очищаем историю задач, перешедших в закрытый статус
                    for event in new_events:
                        if event.event_type == "status_change" and event.to_status in CLOSED_GROUP:
                            channel.processed_events.pop(event.issue_key, None)

                    await self._save_state()

            channel.last_check = utc_now_naive()

        except Exception:
            logger.exception("Error checking channel %s", channel.user)

    async def _send_events(self, chat_id: int, events: list[JiraEvent], marker: str | None = None) -> None:
        """Отправляет уведомления о событиях с маркером канала."""
        if not self._bot:
            return

        for i, event in enumerate(events):
            if i > 0:
                await asyncio.sleep(SEND_DELAY_SECONDS)
            await self._send_one(chat_id, event, marker)

    async def _send_one(self, chat_id: int, event: JiraEvent, marker: str | None = None) -> None:
        """Отправляет одно событие с retry на TelegramRetryAfter (HTTP 429)."""
        for attempt in range(2):
            try:
                await self._bot.send_message(
                    chat_id,
                    self._format_event(event, marker),
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

    def _format_event(self, event: JiraEvent, marker: str | None = None) -> str:
        """Форматирует событие. marker (эмодзи канала коллеги) идёт впереди event-type иконки."""
        icon = EVENT_ICONS.get(event.event_type, "📌")
        title = EVENT_TITLES.get(event.event_type, "Обновление")
        header = f"{marker} {icon}" if marker else icon
        lines = [
            f"{header} {hbold(title)}",
            f"{hlink(event.issue_key, event.issue_url)}: {event.issue_summary}",
            f"От: {event.author}",
            f"{event.details}",
        ]
        return "\n".join(lines)


# Глобальный экземпляр сервиса уведомлений
notification_service = NotificationService()
