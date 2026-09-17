"""Полная fail-closed проверка notification state и интервалов service boundary."""
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bot.services.notifications as nots


def _valid_channel(**overrides):
    channel = {
        "interval_minutes": 15,
        "emoji": "🔵",
        "cursor_utc": "2026-01-01T12:00:00Z",
        "processed_events": {
            "ABC-1": {"comment-1": "2026-01-01T12:00:30Z"}
        },
    }
    channel.update(overrides)
    return channel


def _current_state(**overrides):
    state = {
        "schema_version": 2,
        "chat_id": 100,
        "channels": {"alice": _valid_channel()},
        "silent_users": ["automation"],
    }
    state.update(overrides)
    return state


@pytest.mark.parametrize(
    "invalid_state",
    [
        [],
        _current_state(schema_version=True),
        _current_state(schema_version=3),
        _current_state(chat_id="100"),
        _current_state(chat_id=0),
        _current_state(chat_id=None),
        _current_state(channels=[]),
        _current_state(channels={"": _valid_channel()}),
        _current_state(channels={"alice\n": _valid_channel()}),
        _current_state(silent_users={"automation": True}),
        _current_state(silent_users=["automation", 7]),
        _current_state(silent_users=[""]),
        _current_state(silent_users=["automation\x00"]),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": ["🔵"],
                    "cursor_utc": "2026-01-01T12:00:00Z",
                    "processed_events": {},
                }
            }
        ),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": "🔵",
                    "cursor_utc": 123,
                    "processed_events": {},
                }
            }
        ),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": "🔵",
                    "cursor_utc": "not-a-timestamp",
                    "processed_events": {},
                }
            }
        ),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": "🔵",
                    "cursor_utc": "2026-01-01T12:00:00Z",
                    "processed_events": {"ABC-1": 7},
                }
            }
        ),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": "🔵",
                    "cursor_utc": "2026-01-01T12:00:00Z",
                    "processed_events": {"ABC-1": ["ok", 7]},
                }
            }
        ),
        _current_state(
            channels={
                "alice": {
                    "interval_minutes": 15,
                    "emoji": "🔵",
                    "cursor_utc": "2026-01-01T12:00:00Z",
                    "processed_events": {
                        "ABC-1": {"comment-1": False}
                    },
                }
            }
        ),
    ],
    ids=[
        "root",
        "schema-type",
        "schema-version",
        "chat-id-type",
        "chat-id-zero",
        "channels-without-chat",
        "channels",
        "empty-channel-user",
        "control-in-channel-user",
        "silent-users-container",
        "silent-user-type",
        "empty-silent-user",
        "control-in-silent-user",
        "emoji",
        "cursor-type",
        "cursor-value",
        "dedup-container",
        "dedup-event-id",
        "dedup-timestamp",
    ],
)
def test_invalid_state_is_rejected_without_changing_file(state_path, invalid_state):
    original = json.dumps(invalid_state)
    state_path.write_text(original)

    service = nots.NotificationService(state_file=state_path)

    assert isinstance(service.state_load_error, nots.StateLoadError)
    assert isinstance(service.state_load_error, nots.StateSaveError)
    assert service._chat_id is None
    assert service.list_channels() == []
    assert service.get_silent_users() == set()
    assert state_path.read_text() == original


def test_negative_chat_and_jira_identity_characters_are_preserved(state_path):
    identity = 'user"name\\кириллица'
    state = _current_state(
        chat_id=-100123,
        channels={identity: _valid_channel()},
        silent_users=[identity],
    )
    state_path.write_text(json.dumps(state))

    service = nots.NotificationService(state_file=state_path)

    assert service.state_load_error is None
    assert service._chat_id == -100123
    assert service.get_channel(identity) is not None
    assert service.get_silent_users() == {identity}


def test_invalid_last_channel_rejects_valid_channels_too(state_path):
    state = _current_state()
    state["channels"]["broken-last"] = {
        "interval_minutes": 15,
        "emoji": "🟢",
        "cursor_utc": "2026-01-01T12:01:00Z",
        "processed_events": {"XYZ-2": {"comment-2": 123}},
    }
    state_path.write_text(json.dumps(state))

    service = nots.NotificationService(state_file=state_path)

    assert service.state_load_error is not None
    assert service._chat_id is None
    assert service.get_channel("alice") is None
    assert service.get_channel("broken-last") is None


@pytest.mark.parametrize("interval", [True, False, 0, -1, 1441, 15.0, "15"])
def test_invalid_persisted_interval_is_rejected_strictly(state_path, interval):
    state = _current_state()
    state["channels"]["alice"]["interval_minutes"] = interval
    original = json.dumps(state)
    state_path.write_text(original)

    service = nots.NotificationService(state_file=state_path)

    assert service.state_load_error is not None
    assert service.list_channels() == []
    assert state_path.read_text() == original


@pytest.mark.parametrize("interval", [1, 1440])
def test_persisted_interval_boundaries_are_valid(state_path, interval):
    state = _current_state()
    state["channels"]["alice"]["interval_minutes"] = interval
    state_path.write_text(json.dumps(state))

    service = nots.NotificationService(state_file=state_path)

    assert service.state_load_error is None
    assert service.get_channel("alice").interval_minutes == interval


@pytest.mark.parametrize(
    "legacy_state, expected_user, expected_events",
    [
        (
            {
                "chat_id": 100,
                "interval_minutes": 15,
                "processed_ids": ["unscoped-1"],
                "silent_users": ["automation"],
            },
            nots.PERSONAL,
            {},
        ),
        (
            {
                "chat_id": 100,
                "channels": {
                    "alice": {
                        "interval_minutes": 15,
                        "emoji": "🔵",
                        "processed_events": {"ABC-1": ["comment-1"]},
                    }
                },
                "silent_users": [],
            },
            "alice",
            {"ABC-1": {"comment-1"}},
        ),
    ],
    ids=["flat", "channels-without-cursor"],
)
def test_supported_legacy_schemas_still_migrate(
    state_path, legacy_state, expected_user, expected_events
):
    state_path.write_text(json.dumps(legacy_state))

    service = nots.NotificationService(state_file=state_path)

    assert service.state_load_error is None
    channel = service.get_channel(expected_user)
    assert channel is not None
    assert channel.processed_events == expected_events
    assert channel.last_check is not None
    assert json.loads(state_path.read_text())["schema_version"] == 2


@pytest.mark.asyncio
async def test_load_error_blocks_start_and_later_state_write(state_path, fake_jira):
    original = "{not-json"
    state_path.write_text(original)
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)
    load_error = service.state_load_error

    with pytest.raises(nots.StateLoadError) as start_error:
        service.start(AsyncMock())
    assert start_error.value is load_error
    assert service._bot is None
    assert service._tasks == {}

    with pytest.raises(nots.StateLoadError) as write_error:
        await service.enable_personal(100, 15)
    assert write_error.value is load_error
    assert state_path.read_text() == original
    assert service._chat_id is None
    assert service.list_channels() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [True, False, 0, -1, 1441, 15.0, "15"])
async def test_enable_personal_rejects_invalid_interval_before_persistence(
    state_path, fake_jira, interval
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)

    with pytest.raises(ValueError):
        await service.enable_personal(100, interval)

    assert service._chat_id is None
    assert service.list_channels() == []
    assert not state_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [True, False, 0, -1, 1441, 15.0, "15"])
async def test_track_colleague_rejects_invalid_interval_before_probe(
    state_path, fake_jira, interval
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)

    with pytest.raises(ValueError):
        await service.track_colleague(100, "alice", interval=interval)

    fake_jira.has_visible_assigned_tasks.assert_not_awaited()
    assert service._chat_id is None
    assert service.list_channels() == []
    assert not state_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["subscribe", "add_channel", "update_interval"])
@pytest.mark.parametrize("interval", [True, False, 0, -1, 1441, 15.0, "15"])
async def test_other_public_service_boundaries_reject_invalid_interval(
    state_path, fake_jira, method, interval
):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)

    with pytest.raises(ValueError):
        if method == "subscribe":
            await service.subscribe(100, interval)
        elif method == "add_channel":
            await service.add_channel("alice", interval=interval)
        else:
            await service.update_interval(100, interval)

    assert service._chat_id is None
    assert service.list_channels() == []
    assert not state_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [1, 1440])
async def test_service_interval_boundaries_persist(state_path, fake_jira, interval):
    service = nots.NotificationService(jira=fake_jira, state_file=state_path)

    outcome = await service.enable_personal(100, interval)

    assert outcome.interval == interval
    assert service.get_interval() == interval
    saved = json.loads(state_path.read_text())
    assert saved["channels"][nots.PERSONAL]["interval_minutes"] == interval
