import asyncio
import json
import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.utils.formatting import Bold, Text

from bot.access import AccessPolicy, access_policy
from bot.config import settings
from bot.intervals import validate_interval
from bot.render import issue_ref, split_message
from bot.services.jira import jira_service, JiraEvent, utc_now_naive

logger = logging.getLogger(__name__)

# Задержка между уведомлениями, чтобы не упереться в rate-limit Telegram
SEND_DELAY_SECONDS = 0.5

# Повторяем последний участок окна: покрывает доставку, рестарт и задержки индекса Jira.
EVENT_REPLAY_OVERLAP = timedelta(minutes=10)
# Подтверждения нужны, пока соответствующий участок может попасть в replay-окно.
DEDUP_RETENTION = EVENT_REPLAY_OVERLAP

# Telegram: ограниченные повторы временных ошибок одного фрагмента.
SEND_MAX_ATTEMPTS = 3
SEND_RETRY_INITIAL_SECONDS = 1
SEND_RETRY_MAX_SECONDS = 30

# Ошибочный канал повторяем быстро, но с ограниченной экспоненциальной паузой.
ERROR_RETRY_INITIAL_SECONDS = 15
ERROR_RETRY_MAX_SECONDS = 300

STATE_SCHEMA_VERSION = 2

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
    # Дедуп на канал: {issue_key: set(event_ids)}. Времена нужны для pruning replay-окна.
    processed_events: dict[str, set[str]] = field(default_factory=dict)
    processed_event_times: dict[str, dict[str, datetime]] = field(default_factory=dict)
    last_check: datetime | None = None
    # Не сериализуются: check_lock дренирует текущую проверку, active немедленно
    # ограждает удаляемый канал, пока durable snapshot ещё записывается.
    check_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    active: bool = field(default=True, repr=False, compare=False)

    @property
    def is_personal(self) -> bool:
        return self.user == PERSONAL

    @property
    def jira_target(self) -> str | None:
        """target для jira_service.get_events_since: None у личного канала."""
        return None if self.is_personal else self.user


@dataclass
class EnableOutcome:
    """Итог enable_personal — хендлер по нему рендерит ответ."""

    status: str  # "enabled" | "interval_changed" | "unchanged" | "chat_busy"
    interval: int = 0
    old_interval: int = 0


@dataclass
class TrackOutcome:
    """Итог track_colleague — хендлер по нему рендерит ответ."""

    status: str  # "tracked" | "chat_busy" | "probe_failed"
    channel: Channel | None = None
    has_visible_assigned_tasks: bool = False


class StateSaveError(RuntimeError):
    """Состояние уведомлений не удалось надёжно сохранить."""


class StateLoadError(StateSaveError):
    """Сохранённое состояние не прошло полную проверку при загрузке."""


class NotificationService:
    """Сервис отправки уведомлений о событиях Jira по независимым каналам."""

    # Интервал проверки по умолчанию в минутах
    DEFAULT_INTERVAL_MINUTES = 30

    def __init__(
        self,
        jira=jira_service,
        state_file: Path | None = None,
        first_check_delay: float = FIRST_CHECK_DELAY_SECONDS,
        policy: AccessPolicy = access_policy,
    ) -> None:
        # Коллаборанты принимаются, а не создаются — так интерфейс становится тестовой
        # поверхностью (в тестах подставляются фейки без monkeypatch глобалов).
        self._jira = jira  # источник событий Jira (нужен только get_events_since)
        self._access_policy = policy
        self._state_file = state_file  # None → путь берём из settings динамически
        self._first_check_delay = first_check_delay  # задержка первой проверки канала
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
        # После сбоя записи не отправляем новые события до успешного durable snapshot.
        self._save_failed = False
        # Невалидный файл нельзя молча заменить пустым состоянием поздней командой.
        self._state_load_error: StateLoadError | None = None
        # Схема без cursor сначала получает baseline и сохраняется до первого опроса.
        self._migration_pending = False
        # Сериализует все изменения subscribed chat, каналов и muted authors.
        # Порядок блокировок: lifecycle → channel.check_lock → save. Poll берёт
        # только check_lock → save; код под check/save никогда не берёт lifecycle.
        self._lifecycle_lock = asyncio.Lock()
        # Загружаем сохранённое состояние при инициализации
        self._load_state()

    # ---- Состояние -------------------------------------------------------

    def _state_path(self) -> Path:
        """Путь к файлу состояния: инъекция из конструктора или settings по умолчанию."""
        return self._state_file if self._state_file is not None else settings.state_file

    @property
    def state_load_error(self) -> StateLoadError | None:
        """Публичный статус ошибки загрузки, блокирующей старт и перезапись файла."""
        return self._state_load_error

    @property
    def subscribed_chat_allowed(self) -> bool:
        """Можно ли доставлять в текущий subscribed chat по активной policy."""
        return self._chat_id is None or self._access_policy.allows_delivery(self._chat_id)

    def _load_state(self) -> None:
        """Проверяет весь файл во временных структурах и лишь затем устанавливает state."""
        state_file = self._state_path()
        if not state_file.exists():
            return

        try:
            data = json.loads(state_file.read_text())
            if not isinstance(data, dict):
                raise ValueError("notification state root must be an object")

            if "schema_version" in data:
                schema_version = data["schema_version"]
                if type(schema_version) is not int:
                    raise ValueError("state schema version must be an integer")
                if schema_version != STATE_SCHEMA_VERSION:
                    raise ValueError("unsupported state schema version")
            else:
                schema_version = None

            chat_id = data.get("chat_id")
            if chat_id is not None and (type(chat_id) is not int or chat_id == 0):
                raise ValueError("chat_id must be a non-zero integer or null")

            raw_silent_users = data.get("silent_users", [])
            if not isinstance(raw_silent_users, list):
                raise ValueError("silent_users must be an array of strings")
            silent_users = {
                self._validate_state_identity(user, "silent user")
                for user in raw_silent_users
            }

            channels: dict[str, Channel] = {}
            baseline = utc_now_naive()
            migration_needed = False

            if "channels" in data:
                raw_channels = data["channels"]
                if not isinstance(raw_channels, dict):
                    raise ValueError("channels must be an object")
                if chat_id is None and raw_channels:
                    raise ValueError("channels require a subscribed chat")
                for user, raw_channel in raw_channels.items():
                    user = self._validate_state_identity(user, "channel user")
                    if not isinstance(raw_channel, dict):
                        raise ValueError("invalid channel state")

                    interval = validate_interval(
                        raw_channel.get(
                            "interval_minutes", self.DEFAULT_INTERVAL_MINUTES
                        )
                    )
                    emoji = raw_channel.get("emoji")
                    if emoji is not None and not isinstance(emoji, str):
                        raise ValueError("channel emoji must be a string or null")

                    if "cursor_utc" in raw_channel:
                        cursor = self._parse_utc_timestamp(raw_channel["cursor_utc"])
                    elif schema_version is None:
                        # Старая канальная схема не хранила cursor. Не рассылаем всю историю.
                        cursor = baseline
                        migration_needed = True
                    else:
                        raise ValueError("current notification state is missing cursor_utc")
                    if cursor > baseline:
                        logger.warning("Future notification cursor clamped for channel %s", user)
                        cursor = baseline
                        migration_needed = True
                    processed, processed_times = self._load_processed_events(
                        raw_channel.get("processed_events", {}), cursor
                    )
                    channels[user] = Channel(
                        user=user,
                        interval_minutes=interval,
                        emoji=emoji,
                        processed_events=processed,
                        processed_event_times=processed_times,
                        last_check=cursor,
                    )
            else:
                if schema_version is not None:
                    raise ValueError("current notification state is missing channels")
                # Плоская схема становится личным каналом с одним migration-baseline.
                interval = validate_interval(
                    data.get("interval_minutes", self.DEFAULT_INTERVAL_MINUTES)
                )
                raw = data.get("processed_events", data.get("processed_ids", []))
                processed, processed_times = self._load_processed_events(raw, baseline)
                if chat_id is not None:
                    channels[PERSONAL] = Channel(
                        user=PERSONAL,
                        interval_minutes=interval,
                        emoji=None,
                        processed_events=processed,
                        processed_event_times=processed_times,
                        last_check=baseline,
                    )
                    migration_needed = True
        except Exception as error:
            # Не логируем значения из потенциально повреждённого файла.
            self._state_load_error = StateLoadError(
                "Сохранённое состояние уведомлений не прошло проверку"
            )
            logger.error(
                "Error loading notification state; polling disabled (error_type=%s)",
                type(error).__name__,
            )
            return

        self._chat_id = chat_id
        self._silent_users = silent_users
        self._channels = channels
        self._migration_pending = migration_needed
        if migration_needed:
            try:
                # Baseline должен пережить рестарт ещё до запуска фоновых проверок.
                self._write_state(self._serialize_state())
                self._migration_pending = False
            except Exception:
                self._save_failed = True
                logger.exception("Error persisting migrated notification state")
        if chat_id is not None:
            logger.info("Restored %d channel(s) (chat_id=%s)", len(channels), chat_id)

    @staticmethod
    def _validate_state_identity(value: object, field: str) -> str:
        """Проверяет минимум, общий для persisted Jira identities."""
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError(f"{field} must not contain control characters")
        return value

    @staticmethod
    def _format_utc_timestamp(value: datetime) -> str:
        if value.tzinfo is not None:
            raise ValueError("Notification timestamps must be naive UTC")
        return f"{value.isoformat()}Z"

    @staticmethod
    def _parse_utc_timestamp(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("notification timestamp must be a string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Принимаем короткоживущий промежуточный формат до schema_version=2.
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _load_processed_events(
        cls, raw: object, fallback: datetime
    ) -> tuple[dict[str, set[str]], dict[str, dict[str, datetime]]]:
        """Читает timestamp-map новой схемы и списки ID старой схемы."""
        processed: dict[str, set[str]] = {}
        times: dict[str, dict[str, datetime]] = {}
        if isinstance(raw, list):
            # Совсем старый глобальный список нельзя безопасно привязать к задачам,
            # но его элементы всё равно должны соответствовать старой схеме.
            if not all(isinstance(event_id, str) for event_id in raw):
                raise ValueError("event id must be a string")
            return processed, times
        if not isinstance(raw, dict):
            raise ValueError("processed_events must be an object")
        for issue_key, raw_ids in raw.items():
            if not isinstance(issue_key, str):
                raise ValueError("issue key must be a string")
            if isinstance(raw_ids, dict):
                parsed = {
                    event_id: cls._parse_utc_timestamp(raw_timestamp)
                    for event_id, raw_timestamp in raw_ids.items()
                    if isinstance(event_id, str)
                }
                if len(parsed) != len(raw_ids):
                    raise ValueError("event id must be a string")
            elif isinstance(raw_ids, list):
                if not all(isinstance(event_id, str) for event_id in raw_ids):
                    raise ValueError("event id must be a string")
                parsed = {event_id: fallback for event_id in raw_ids}
            else:
                raise ValueError("issue event history must be an object or array")
            if parsed:
                processed[issue_key] = set(parsed)
                times[issue_key] = parsed
        return processed, times

    def _serialize_state(
        self,
        channel_override: tuple[
            Channel,
            datetime,
            dict[str, set[str]],
            dict[str, dict[str, datetime]],
        ]
        | None = None,
        state_override: tuple[int | None, dict[str, Channel], set[str]] | None = None,
    ) -> str:
        """Строит JSON текущего состояния либо ещё не опубликованного lifecycle-кандидата."""
        fallback = utc_now_naive()
        override_channel = channel_override[0] if channel_override else None
        if state_override is None:
            chat_id, channels, silent_users = (
                self._chat_id,
                self._channels,
                self._silent_users,
            )
        else:
            chat_id, channels, silent_users = state_override
        channel_data = {}
        for user in sorted(channels):
            channel = channels[user]
            if channel is override_channel:
                _, cursor, processed, processed_times = channel_override
            else:
                cursor = channel.last_check
                processed = channel.processed_events
                processed_times = channel.processed_event_times
            # Активный канал всегда сохраняем с cursor, даже если тестовый или
            # восстановленный объект был создан без baseline.
            cursor = cursor or fallback
            channel_data[user] = {
                "interval_minutes": channel.interval_minutes,
                "emoji": channel.emoji,
                "cursor_utc": self._format_utc_timestamp(cursor),
                "processed_events": {
                    issue_key: {
                        event_id: self._format_utc_timestamp(
                            processed_times.get(issue_key, {}).get(
                                event_id, cursor or fallback
                            )
                        )
                        for event_id in sorted(processed[issue_key])
                    }
                    for issue_key in sorted(processed)
                },
            }
        data = {
            "schema_version": STATE_SCHEMA_VERSION,
            "chat_id": chat_id,
            "channels": channel_data,
            "silent_users": sorted(silent_users),
        }
        return json.dumps(data, sort_keys=True)

    def _write_state(self, payload: str) -> None:
        """Атомарно пишет уже сериализованную строку (может выполняться в to_thread)."""
        if self._state_load_error is not None:
            raise self._state_load_error
        state_file = self._state_path()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # Пишем во временный файл и атомарно подменяем — иначе падение бота посреди
        # write_text оставит обрезанный JSON, а _load_state молча сбросит подписку.
        tmp = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(state_file)
        logger.info("Subscription state saved")

    def _save_state_sync(self) -> None:
        """Синхронное сохранение (тесты / прямые вызовы)."""
        try:
            payload = self._serialize_state()
            self._write_state(payload)
        except Exception:
            logger.exception("Error saving state")

    async def _write_payload(
        self, payload: str, on_success: Callable[[], None] | None = None
    ) -> None:
        """Дожидается disk-thread и публикует snapshot до передачи cancellation."""
        write_task = asyncio.create_task(asyncio.to_thread(self._write_state, payload))
        cancelled = False
        while not write_task.done():
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError:
                # to_thread нельзя отменить. Не выпускаем save_lock и не оставляем
                # durable snapshot без соответствующей публикации в памяти.
                cancelled = True
            except Exception:
                break
        try:
            write_task.result()
        except StateLoadError:
            raise
        except Exception as error:
            logger.exception("Error saving state")
            raise StateSaveError("Не удалось записать состояние") from error
        if on_success is not None:
            on_success()
        if cancelled:
            raise asyncio.CancelledError

    async def _save_state(self) -> None:
        """Сохраняет актуальный снапшот, не отпуская lock раньше потока записи."""
        async with self._save_lock:
            try:
                # Снапшот строим только после получения lock: ожидавший save не должен
                # записать состояние, устаревшее за время ожидания.
                payload = self._serialize_state()
            except Exception as error:
                logger.exception("Error serializing state")
                raise StateSaveError("Не удалось сериализовать состояние") from error
            await self._write_payload(payload)

    async def _persist_lifecycle_state(
        self,
        chat_id: int | None,
        channels: dict[str, Channel],
        silent_users: set[str],
        publish: Callable[[], None],
    ) -> None:
        """Сохраняет lifecycle-кандидат и только затем синхронно публикует его."""
        async with self._save_lock:
            try:
                payload = self._serialize_state(
                    state_override=(chat_id, channels, silent_users)
                )
            except Exception as error:
                logger.exception("Error serializing state")
                raise StateSaveError("Не удалось сериализовать состояние") from error
            await self._write_payload(payload, publish)

    async def _commit_poll_cursor(self, channel: Channel, cursor: datetime) -> bool:
        """Атомарно сохраняет новый cursor с дедупом, не публикуя его в памяти заранее."""
        processed = {key: set(ids) for key, ids in channel.processed_events.items()}
        processed_times = {
            key: dict(times) for key, times in channel.processed_event_times.items()
        }
        self._prune_processed_events(processed, processed_times, cursor)

        async with self._save_lock:
            if self._channels.get(channel.user) is not channel:
                return False
            try:
                payload = self._serialize_state(
                    (channel, cursor, processed, processed_times)
                )
            except Exception as error:
                logger.exception("Error serializing poll state")
                raise StateSaveError("Не удалось сериализовать состояние") from error

            committed = False

            def publish() -> None:
                nonlocal committed
                # Публикация идёт и при cancellation после завершения disk-thread.
                if self._channels.get(channel.user) is channel:
                    channel.last_check = cursor
                    channel.processed_events = processed
                    channel.processed_event_times = processed_times
                    committed = True

            await self._write_payload(payload, publish)
            return committed

    # ---- Привязка чата и каналы ------------------------------------------

    def _resolve_marker(self, emoji: str | None) -> str | None:
        """Возвращает маркер: явный или следующий свободный из палитры."""
        if emoji is not None:
            return emoji
        used = {ch.emoji for ch in self._channels.values() if ch.emoji}
        for marker in MARKER_PALETTE:
            if marker not in used:
                return marker
        return MARKER_PALETTE[0]  # палитра исчерпана — переиспользуем первый

    @staticmethod
    def _channel_with_settings(
        channel: Channel, interval_minutes: int, emoji: str | None
    ) -> Channel:
        """Кандидат настроек для JSON; действующий объект канала не заменяется."""
        return Channel(
            user=channel.user,
            interval_minutes=interval_minutes,
            emoji=emoji,
            processed_events=channel.processed_events,
            processed_event_times=channel.processed_event_times,
            last_check=channel.last_check,
            check_lock=channel.check_lock,
            active=channel.active,
        )

    async def _upsert_channel_locked(
        self,
        chat_id: int,
        user: str,
        emoji: str | None,
        interval: int | None,
    ) -> Channel:
        """Persist-first upsert. Caller holds lifecycle_lock and has checked chat owner."""
        existing = self._channels.get(user)
        if existing is not None:
            # Poll заменяет cursor/dedup dict после durable commit. Берём check_lock
            # до построения кандидата, иначе ожидавший save_lock мог записать старый cursor.
            async with existing.check_lock:
                new_interval = interval or existing.interval_minutes
                new_emoji = emoji if emoji is not None else existing.emoji
                proposed_channels = dict(self._channels)
                proposed_channels[user] = self._channel_with_settings(
                    existing, new_interval, new_emoji
                )

                def publish() -> None:
                    self._chat_id = chat_id
                    # Сохраняем identity: removal fencing сравнивает объект по `is`.
                    existing.interval_minutes = new_interval
                    existing.emoji = new_emoji

                await self._persist_lifecycle_state(
                    chat_id, proposed_channels, set(self._silent_users), publish
                )
            return existing

        channel = Channel(
            user=user,
            interval_minutes=interval or self.DEFAULT_INTERVAL_MINUTES,
            emoji=self._resolve_marker(emoji),
            last_check=utc_now_naive(),
        )
        proposed_channels = dict(self._channels)
        proposed_channels[user] = channel

        def publish() -> None:
            self._chat_id = chat_id
            self._channels[user] = channel
            self._start_channel_task(channel)

        await self._persist_lifecycle_state(
            chat_id, proposed_channels, set(self._silent_users), publish
        )
        return channel

    async def add_channel(
        self, user: str, emoji: str | None = None, interval: int | None = None
    ) -> Channel:
        """Создаёт или обновляет канал коллеги; subscribed chat уже должен быть привязан."""
        if interval is not None:
            interval = validate_interval(interval)
        async with self._lifecycle_lock:
            if self._chat_id is None:
                raise RuntimeError("Cannot add a channel without a subscribed chat")
            return await self._upsert_channel_locked(
                self._chat_id, user, emoji, interval
            )

    async def track_colleague(
        self, chat_id: int, user: str, emoji: str | None = None, interval: int | None = None
    ) -> TrackOutcome:
        """Проверяет Jira без привязки, затем атомарно перепроверяет chat и создаёт канал."""
        if interval is not None:
            interval = validate_interval(interval)
        async with self._lifecycle_lock:
            if self._chat_id is not None and self._chat_id != chat_id:
                return TrackOutcome("chat_busy")

        # Сетевую пробу нельзя держать под lifecycle_lock: управление существующими
        # каналами должно продолжаться. Неудачная проба теперь не оставляет пустую привязку.
        try:
            has_visible_tasks = await self._jira.has_visible_assigned_tasks(user)
        except Exception:
            logger.exception("track probe failed for %s", user)
            return TrackOutcome("probe_failed")

        async with self._lifecycle_lock:
            # За время probe последний канал мог удалиться, а другой chat — привязаться.
            if self._chat_id is not None and self._chat_id != chat_id:
                return TrackOutcome("chat_busy")
            channel = await self._upsert_channel_locked(
                chat_id, user, emoji, interval
            )
        return TrackOutcome(
            "tracked",
            channel=channel,
            has_visible_assigned_tasks=has_visible_tasks,
        )

    async def remove_channel(self, chat_id: int, user: str) -> bool:
        """Убирает канал коллеги только по запросу из привязанного чата."""
        if user == PERSONAL:
            return False
        return await self._remove_channel_internal(user, expected_chat_id=chat_id)

    async def _remove_channel_internal(
        self, user: str, expected_chat_id: int | None = None
    ) -> bool:
        async with self._lifecycle_lock:
            if expected_chat_id is not None and self._chat_id != expected_chat_id:
                return False

            channel = self._channels.get(user)
            if channel is None or not channel.active:
                return False
            previous_chat_id = self._chat_id
            channel.active = False  # немедленный fence без публикации tentative JSON
            committed = False

            def rollback() -> None:
                channel.active = True
                self._chat_id = previous_chat_id
                self._channels[user] = channel
                self._start_channel_task(channel)

            try:
                await self._cancel_channel_task(user)
                # check_now выполняется вне фоновой задачи. Дожидаемся такой проверки;
                # active=False остановит её до отправки, сохранив старый JSON до commit.
                async with channel.check_lock:
                    pass

                proposed_channels = dict(self._channels)
                proposed_channels.pop(user, None)
                proposed_chat_id = previous_chat_id if proposed_channels else None

                def publish() -> None:
                    nonlocal committed
                    if self._channels.get(user) is channel:
                        self._channels.pop(user)
                    self._chat_id = proposed_chat_id
                    committed = True

                await self._persist_lifecycle_state(
                    proposed_chat_id,
                    proposed_channels,
                    set(self._silent_users),
                    publish,
                )
            except StateSaveError:
                rollback()
                raise
            except asyncio.CancelledError:
                # _write_payload вызывает publish после завершения disk-thread. До этой
                # границы отмена означает rollback; после неё removal уже durable.
                if not committed:
                    rollback()
                raise
            return True

    def list_channels(self) -> list[Channel]:
        """Активные каналы: личный первым, коллеги по имени."""
        return sorted(
            (channel for channel in self._channels.values() if channel.active),
            key=lambda c: (not c.is_personal, c.user),
        )

    def get_channel(self, user: str) -> Channel | None:
        channel = self._channels.get(user)
        return channel if channel is not None and channel.active else None

    # ---- Личный канал (/sync, /unsync) -----------------------------------

    async def _subscribe_locked(
        self, chat_id: int, interval_minutes: int | None
    ) -> bool:
        if self._chat_id is not None and self._chat_id != chat_id:
            return False
        existing = self._channels.get(PERSONAL)
        if existing is not None and existing.active:
            return False
        channel = Channel(
            user=PERSONAL,
            interval_minutes=interval_minutes or self.DEFAULT_INTERVAL_MINUTES,
            emoji=None,
            last_check=utc_now_naive(),
        )
        proposed_channels = dict(self._channels)
        proposed_channels[PERSONAL] = channel

        def publish() -> None:
            self._chat_id = chat_id
            self._channels[PERSONAL] = channel
            self._start_channel_task(channel)

        await self._persist_lifecycle_state(
            chat_id, proposed_channels, set(self._silent_users), publish
        )
        logger.info(
            "Subscribed personal channel (interval: %d min)",
            channel.interval_minutes,
        )
        return True

    async def subscribe(self, chat_id: int, interval_minutes: int | None = None) -> bool:
        """Подписывает личный канал. False — чат занят другим или уже подписан."""
        if interval_minutes is not None:
            interval_minutes = validate_interval(interval_minutes)
        async with self._lifecycle_lock:
            return await self._subscribe_locked(chat_id, interval_minutes)

    async def unsubscribe(self, chat_id: int) -> bool:
        """Отписывает личный канал (каналы коллег остаются)."""
        if not await self._remove_channel_internal(PERSONAL, expected_chat_id=chat_id):
            return False
        logger.info("Unsubscribed personal channel")
        return True

    def is_subscribed(self, chat_id: int) -> bool:
        """Подписан ли личный канал в этом чате."""
        channel = self._channels.get(PERSONAL)
        return self._chat_id == chat_id and channel is not None and channel.active

    def get_interval(self) -> int:
        """Интервал личного канала."""
        channel = self.get_channel(PERSONAL)
        return channel.interval_minutes if channel else self.DEFAULT_INTERVAL_MINUTES

    async def _update_interval_locked(
        self, chat_id: int, interval_minutes: int
    ) -> bool:
        channel = self._channels.get(PERSONAL)
        if self._chat_id != chat_id or channel is None or not channel.active:
            return False
        # Не копируем poll state до check_lock: poll может ждать save_lock и затем
        # заменить cursor/dedup-ссылки, пока interval update ожидает запись.
        async with channel.check_lock:
            proposed_channels = dict(self._channels)
            proposed_channels[PERSONAL] = self._channel_with_settings(
                channel, interval_minutes, channel.emoji
            )

            def publish() -> None:
                channel.interval_minutes = interval_minutes

            await self._persist_lifecycle_state(
                self._chat_id, proposed_channels, set(self._silent_users), publish
            )
        logger.info("Updated personal interval to %d min", interval_minutes)
        return True

    async def update_interval(self, chat_id: int, interval_minutes: int) -> bool:
        """Обновляет интервал личного канала без замены объекта Channel."""
        interval_minutes = validate_interval(interval_minutes)
        async with self._lifecycle_lock:
            return await self._update_interval_locked(chat_id, interval_minutes)

    async def enable_personal(
        self, chat_id: int, interval_minutes: int | None = None
    ) -> EnableOutcome:
        """Включает/обновляет personal channel одной lifecycle-транзакцией."""
        if interval_minutes is not None:
            interval_minutes = validate_interval(interval_minutes)
        async with self._lifecycle_lock:
            if self._chat_id is not None and self._chat_id != chat_id:
                return EnableOutcome("chat_busy")
            channel = self._channels.get(PERSONAL)
            if channel is not None and channel.active:
                current = channel.interval_minutes
                new = interval_minutes or current
                if new != current:
                    await self._update_interval_locked(chat_id, new)
                    return EnableOutcome(
                        "interval_changed", interval=new, old_interval=current
                    )
                return EnableOutcome("unchanged", interval=current)
            await self._subscribe_locked(chat_id, interval_minutes)
            return EnableOutcome(
                "enabled", interval=interval_minutes or self.DEFAULT_INTERVAL_MINUTES
            )

    async def check_now(self, user: str = PERSONAL) -> None:
        """Немедленная проверка канала (по умолчанию — личного)."""
        channel = self.get_channel(user)
        if channel is not None:
            await self._check_channel(channel)

    # ---- Тихий режим (сквозной, по автору события) -----------------------

    async def mute_user(self, username: str) -> None:
        async with self._lifecycle_lock:
            proposed = set(self._silent_users)
            proposed.add(username)
            await self._persist_lifecycle_state(
                self._chat_id,
                dict(self._channels),
                proposed,
                lambda: self._silent_users.add(username),
            )

    async def unmute_user(self, username: str) -> None:
        async with self._lifecycle_lock:
            proposed = set(self._silent_users)
            proposed.discard(username)
            await self._persist_lifecycle_state(
                self._chat_id,
                dict(self._channels),
                proposed,
                lambda: self._silent_users.discard(username),
            )

    def is_user_silent(self, username: str) -> bool:
        return username in self._silent_users

    def get_silent_users(self) -> set[str]:
        return self._silent_users

    # ---- Фоновые задачи по каналам ---------------------------------------

    def start(self, bot: Bot) -> None:
        """Запускает фоновые задачи, только если subscribed chat всё ещё разрешён."""
        if self._state_load_error is not None:
            raise self._state_load_error
        self._bot = bot
        if not self.subscribed_chat_allowed:
            # Состояние намеренно сохраняем: оператор может исправить policy и рестартовать.
            logger.warning(
                "Notification delivery disabled: subscribed chat is not allowed by access policy"
            )
            return
        for channel in self._channels.values():
            self._start_channel_task(channel)
        logger.info("Notification service started (%d channels)", len(self.list_channels()))

    def _start_channel_task(self, channel: Channel) -> None:
        if self._bot is None or not channel.active:
            return
        task = self._tasks.get(channel.user)
        if task is not None and not task.done():
            return
        self._tasks[channel.user] = asyncio.create_task(self._channel_loop(channel))

    async def _cancel_channel_task(self, user: str) -> None:
        """Отменяет и дожидается фоновой задачи до подтверждения удаления канала."""
        task = self._tasks.pop(user, None)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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
        """Цикл проверки одного канала с ограниченным backoff после ошибок."""
        sleep_secs = self._first_check_delay  # первая проверка вскоре после старта (не ждём полный интервал)
        failure_count = 0
        last_heartbeat: float = 0.0
        while True:
            try:
                await asyncio.sleep(sleep_secs)
                sleep_secs = channel.interval_minutes * 60  # интервал читаем динамически

                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= 3600:
                    logger.info("Channel %s alive (interval=%dm)", channel.user, channel.interval_minutes)
                    last_heartbeat = now

                if await self._check_channel(channel):
                    failure_count = 0
                    sleep_secs = channel.interval_minutes * 60
                else:
                    failure_count += 1
                    sleep_secs = min(
                        ERROR_RETRY_INITIAL_SECONDS * 2 ** min(failure_count - 1, 10),
                        ERROR_RETRY_MAX_SECONDS,
                    )
                    logger.warning("Channel %s retry in %ss (failure %d)", channel.user, sleep_secs, failure_count)
            except asyncio.CancelledError:
                if (
                    self._stopping
                    or not channel.active
                    or self._channels.get(channel.user) is not channel
                ):
                    break
                # Неожиданная внешняя отмена — подавляем только у всё ещё текущего объекта канала.
                asyncio.current_task().uncancel()
                logger.warning("Channel %s loop cancelled unexpectedly, restarting", channel.user)
                sleep_secs = self._first_check_delay
            except Exception:
                logger.exception("Error in channel %s loop", channel.user)

    # ---- Проверка и отправка ---------------------------------------------

    async def _check_channel(self, channel: Channel) -> bool:
        """Проверяет один канал: дедуп на канал (ADR-0002), маркер канала в уведомлении."""
        async with channel.check_lock:
            if (
                self._channels.get(channel.user) is not channel
                or not channel.active
                or not self._bot
                or self._chat_id is None
                or not self._access_policy.allows_delivery(self._chat_id)
                or channel.last_check is None
            ):
                return True

            try:
                if self._save_failed or self._migration_pending:
                    # Общий JSON-файл — единая граница: сначала подтверждаем старый snapshot.
                    await self._save_state()
                    self._save_failed = False
                    self._migration_pending = False
                # Верхнюю границу фиксируем до запроса, нижнюю повторяем с перекрытием.
                window_end = utc_now_naive()
                query_since = channel.last_check - EVENT_REPLAY_OVERLAP
                events = await self._jira.get_events_since(
                    query_since,
                    channel.jira_target,
                    until=window_end,
                )

                # Канал могли удалить, пока Jira-запрос выполнялся в отдельном потоке.
                if (
                    self._channels.get(channel.user) is not channel
                    or not channel.active
                ):
                    return True

                new_events = []
                for event in events:
                    bucket = channel.processed_events.setdefault(event.issue_key, set())
                    if event.id not in bucket:
                        new_events.append(event)

                if new_events:
                    delivered = await self._send_events(
                        self._chat_id, new_events, channel.emoji
                    )
                    for event in delivered:
                        channel.processed_events[event.issue_key].add(event.id)
                        # Время подтверждения, а не время Jira-события, задаёт retention.
                        channel.processed_event_times.setdefault(event.issue_key, {})[
                            event.id
                        ] = window_end
                    if len(delivered) != len(new_events):
                        # Успехи сохраняем, cursor удерживаем: остальные вернутся из replay.
                        await self._save_state()
                        logger.warning(
                            "Channel poll incomplete channel=%s cursor=%s end=%s "
                            "fetched=%d delivered=%d failed=%d",
                            channel.user,
                            channel.last_check,
                            window_end,
                            len(events),
                            len(delivered),
                            len(new_events) - len(delivered),
                        )
                        return False

                if not await self._commit_poll_cursor(channel, window_end):
                    return True
                logger.info(
                    "Channel poll complete channel=%s cursor=%s fetched=%d",
                    channel.user,
                    window_end,
                    len(events),
                )
                return True

            except StateSaveError:
                self._save_failed = True
                logger.exception("Channel poll failed stage=state_save channel=%s cursor=%s", channel.user, channel.last_check)
                return False
            except Exception:
                logger.exception("Channel poll failed stage=fetch_or_delivery channel=%s cursor=%s", channel.user, channel.last_check)
                return False

    @staticmethod
    def _prune_processed_events(
        processed: dict[str, set[str]],
        processed_times: dict[str, dict[str, datetime]],
        cursor: datetime,
    ) -> None:
        """Удаляет только ID, которые уже недостижимы из replay-окна."""
        cutoff = cursor - DEDUP_RETENTION
        for issue_key in list(processed):
            times = processed_times.setdefault(issue_key, {})
            bucket = processed[issue_key]
            for event_id in list(bucket):
                if times.get(event_id, cursor) <= cutoff:
                    bucket.remove(event_id)
                    times.pop(event_id, None)
            if not bucket:
                processed.pop(issue_key, None)
                processed_times.pop(issue_key, None)

    async def _send_events(
        self, chat_id: int, events: list[JiraEvent], marker: str | None = None
    ) -> list[JiraEvent]:
        """Отправляет пакет и возвращает только полностью доставленные события."""
        if not self._bot or not self._access_policy.allows_delivery(chat_id):
            return []

        delivered = []
        for i, event in enumerate(events):
            if i > 0:
                await asyncio.sleep(SEND_DELAY_SECONDS)
            if await self._send_one(chat_id, event, marker):
                delivered.append(event)
        return delivered

    async def _send_one(
        self, chat_id: int, event: JiraEvent, marker: str | None = None
    ) -> bool:
        """Подтверждает событие только после всех фрагментов и ограниченных retry."""
        permanent_errors = (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramUnauthorizedError,
        )
        for chunk in split_message(self._format_event(event, marker)):
            for attempt in range(SEND_MAX_ATTEMPTS):
                try:
                    await self._bot.send_message(
                        chat_id,
                        **chunk.as_kwargs(),
                        disable_notification=event.author_id in self._silent_users,
                    )
                    break
                except TelegramRetryAfter as error:
                    if attempt + 1 == SEND_MAX_ATTEMPTS:
                        logger.error(
                            "Telegram delivery failed class=rate_limit chat=%s "
                            "event=%s attempts=%d",
                            chat_id,
                            event.id,
                            SEND_MAX_ATTEMPTS,
                        )
                        return False
                    logger.warning(
                        "Telegram delivery retry class=rate_limit chat=%s event=%s "
                        "delay=%ss",
                        chat_id,
                        event.id,
                        error.retry_after,
                    )
                    await asyncio.sleep(error.retry_after)
                except permanent_errors as error:
                    logger.error(
                        "Telegram delivery failed class=permanent chat=%s event=%s "
                        "error=%s",
                        chat_id,
                        event.id,
                        type(error).__name__,
                    )
                    return False
                except Exception as error:
                    if attempt + 1 == SEND_MAX_ATTEMPTS:
                        logger.error(
                            "Telegram delivery failed class=transient chat=%s event=%s "
                            "error=%s attempts=%d",
                            chat_id,
                            event.id,
                            type(error).__name__,
                            SEND_MAX_ATTEMPTS,
                        )
                        return False
                    delay = min(
                        SEND_RETRY_INITIAL_SECONDS * 2**attempt,
                        SEND_RETRY_MAX_SECONDS,
                    )
                    logger.warning(
                        "Telegram delivery retry class=transient chat=%s event=%s "
                        "error=%s delay=%ss",
                        chat_id,
                        event.id,
                        type(error).__name__,
                        delay,
                    )
                    await asyncio.sleep(delay)
        return True

    def _format_event(self, event: JiraEvent, marker: str | None = None) -> Text:
        """Форматирует событие. marker (эмодзи канала коллеги) идёт впереди event-type иконки."""
        icon = EVENT_ICONS.get(event.event_type, "📌")
        title = EVENT_TITLES.get(event.event_type, "Обновление")
        header = f"{marker} {icon}" if marker else icon
        return Text(
            header, " ", Bold(title), "\n",
            issue_ref(event.issue_key, event.issue_url, event.issue_summary), "\n",
            "От: ", event.author, "\n",
            event.details,
        )


# Глобальный экземпляр сервиса уведомлений
notification_service = NotificationService()
