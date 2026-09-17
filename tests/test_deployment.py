"""Local regression fixtures for the bounded deployment workflow."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


deploy = _load_script("deploy.py")
startup = _load_script("wait_for_startup.py")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE)


def test_committed_snapshot_is_allowlisted_and_excludes_worktree_secrets(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for relative in deploy.DEPLOY_PATHS:
        path = repo / relative
        if relative == "bot":
            path.mkdir()
            (path / "main.py").write_text("print('bot')\n")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {relative}\n")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    (repo / ".env.local").write_text("TOKEN=must-not-transfer\n")
    (repo / ".pi").mkdir()
    (repo / ".pi" / "runtime.json").write_text("private")

    staging = tmp_path / "staging"
    staging.mkdir()
    revision = deploy.stage_snapshot(repo, "HEAD", staging)

    assert len(revision) == 40
    assert not (staging / ".env.local").exists()
    assert not (staging / ".pi").exists()
    assert (staging / "bot" / "main.py").exists()
    assert (staging / deploy.DEPLOY_METADATA).read_text().strip() == revision


@pytest.mark.skipif(not Path("/usr/bin/rsync").exists(), reason="rsync is required")
def test_rsync_preserves_server_secrets_backups_and_state(tmp_path):
    staging = tmp_path / "staging"
    remote = tmp_path / "remote"
    staging.mkdir()
    remote.mkdir()
    (staging / "bot").mkdir()
    (staging / "bot" / "main.py").write_text("new\n")
    (remote / "bot").mkdir()
    (remote / "bot" / "main.py").write_text("old\n")
    (remote / "bot" / "obsolete.py").write_text("delete managed source\n")

    protected = {
        ".env": "TOKEN=production\n",
        ".env.local": "TOKEN=local\n",
        ".env.bak": "TOKEN=backup\n",
        "server-only.bak": "backup\n",
        "backups/state.json": "state backup\n",
        "secrets/token": "secret\n",
        "data/sync_state.json": "state\n",
        ".pi/runtime.json": "agent runtime\n",
        ".memory/notes.md": "private notes\n",
        "README.md": "unrelated remote documentation\n",
        "arbitrary/owned-by-another-workflow.txt": "leave alone\n",
    }
    for relative, content in protected.items():
        path = remote / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    dry = subprocess.run(
        deploy.rsync_command(staging, f"{remote}/", dry_run=True, rsync_shell="ssh"),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert "bot/obsolete.py" in dry
    assert not any(relative in dry for relative in protected)

    subprocess.run(
        deploy.rsync_command(staging, f"{remote}/", dry_run=False, rsync_shell="ssh"),
        check=True,
        stdout=subprocess.PIPE,
    )
    assert not (remote / "bot" / "obsolete.py").exists()
    assert (remote / "bot" / "main.py").read_text() == "new\n"
    for relative, content in protected.items():
        assert (remote / relative).read_text() == content


def test_configuration_diagnostic_does_not_echo_sensitive_values():
    env = os.environ.copy()
    telegram_secret = "telegram-secret-must-not-leak"
    jira_secret = "jira-secret-must-not-leak"
    invalid_users = "123,private-invalid-user"
    env.update(
        TELEGRAM_TOKEN=telegram_secret,
        JIRA_URL="https://jira.example.invalid",
        JIRA_PAT=jira_secret,
        ALLOWED_USERS=invalid_users,
        PYTHONPATH=str(ROOT),
    )
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "bot.config_check"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert telegram_secret not in output
    assert jira_secret not in output
    assert invalid_users not in output


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


def test_state_diagnostic_does_not_echo_persisted_identities(tmp_path):
    private_identity = "private-tracked-user"
    state_file = tmp_path / "sync_state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "chat_id": -100123,
                "channels": {
                    private_identity: {
                        "interval_minutes": 30,
                        "emoji": None,
                        "cursor_utc": "2999-01-01T00:00:00Z",
                        "processed_events": {},
                    }
                },
                "silent_users": [],
            }
        )
    )
    env = os.environ.copy()
    env.update(
        TELEGRAM_TOKEN="test-token",
        JIRA_URL="http://jira.test",
        JIRA_PAT="test-pat",
        STATE_FILE=str(state_file),
        PYTHONPATH=str(ROOT),
    )
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "bot.state_check"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert private_identity not in output
    assert "-100123" not in output


def test_startup_wait_accepts_initial_polling_delay():
    clock = Clock()
    log_calls = 0

    def docker(*args):
        nonlocal log_calls
        if args[0] == "inspect" and args[2] == "{{.State.StartedAt}}":
            return "2026-09-17T10:00:00Z\n"
        if args[0] == "inspect" and args[2] == "{{.RestartCount}}":
            return "0\n"
        if args[0] == "inspect" and args[2] == "{{.State.Status}}":
            return "running\n"
        if args[0] == "logs":
            log_calls += 1
            return "" if log_calls < 3 else "Run polling for bot @fixture\n"
        raise AssertionError(args)

    assert startup.wait_for_startup(
        "bot", 10, 2, docker=docker, monotonic=clock.monotonic, sleep=clock.sleep
    )
    assert log_calls == 3


def test_startup_wait_rejects_restart_before_observation():
    clock = Clock()

    def docker(*args):
        if args[0] == "inspect" and args[2] == "{{.State.StartedAt}}":
            return "2026-09-17T10:00:00Z\n"
        if args[0] == "inspect" and args[2] == "{{.RestartCount}}":
            return "1\n"
        raise AssertionError(args)

    assert not startup.wait_for_startup(
        "bot", 10, 2, docker=docker, monotonic=clock.monotonic, sleep=clock.sleep
    )


def test_wait_cli_detects_polling_marker_written_to_container_stderr(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *State.StartedAt*) echo '2026-09-17T10:00:00Z' ;;
  *RestartCount*) echo 0 ;;
  *State.Status*) echo running ;;
  logs*) echo 'Run polling for bot @stderr-fixture' >&2 ;;
  *) exit 2 ;;
esac
"""
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/wait_for_startup.py"),
            "fixture-bot",
            "--timeout",
            "1",
            "--interval",
            "0.01",
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0
    assert "current start reached polling" in result.stdout


def _run_remote_deploy_fixture(tmp_path, scenario: str):
    remote = tmp_path / "remote"
    (remote / "scripts").mkdir(parents=True)
    (remote / ".deploy-revision").write_text("a" * 40 + "\n")
    (remote / ".env").write_text("fixture only\n")
    log = tmp_path / "commands.log"
    wait_count = tmp_path / "wait-count"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *'bot.state_snapshot backup'*)
    echo STATE_BACKUP >> "$FAKE_LOG"
    test "$SCENARIO" != backup-failure
    ;;
  *'bot.state_snapshot restore'*)
    echo STATE_RESTORE >> "$FAKE_LOG"
    test "$SCENARIO" != restore-failure
    ;;
  'compose up'*) echo "COMPOSE_UP image=${BOT_IMAGE:-unset}" >> "$FAKE_LOG" ;;
  'compose stop'*) echo COMPOSE_STOP >> "$FAKE_LOG" ;;
  'rm -f'*) echo CONTAINER_REMOVE >> "$FAKE_LOG" ;;
  'ps '*) echo fixture-container ;;
  *'{{.Image}}'*) echo sha256:previous-image ;;
  *'{{.Config.User}}'*) echo 10001:10001 ;;
  'inspect '*) : ;;
  *) : ;;
esac
"""
    )
    docker.chmod(0o755)

    python = fake_bin / "python3"
    python.write_text(
        """#!/usr/bin/env bash
set -eu
echo WAIT >> "$FAKE_LOG"
count=0
test ! -f "$WAIT_COUNT" || count=$(cat "$WAIT_COUNT")
count=$((count + 1))
echo "$count" > "$WAIT_COUNT"
if { test "$SCENARIO" = startup-failure || test "$SCENARIO" = restore-failure; } && test "$count" = 1; then
  exit 1
fi
"""
    )
    python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        DEPLOY_REMOTE=str(remote),
        DEPLOY_WAIT_TIMEOUT="1",
        FAKE_LOG=str(log),
        WAIT_COUNT=str(wait_count),
        SCENARIO=scenario,
    )
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/deploy-remote.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    commands = log.read_text().splitlines() if log.exists() else []
    return result, commands


def test_remote_deploy_success_starts_one_new_container(tmp_path):
    result, commands = _run_remote_deploy_fixture(tmp_path, "success")

    assert result.returncode == 0
    assert commands.count("STATE_BACKUP") == 1
    assert commands.count("STATE_RESTORE") == 0
    assert sum(command.startswith("COMPOSE_UP") for command in commands) == 1
    assert commands.count("WAIT") == 1


def test_remote_startup_failure_restores_snapshot_before_previous_image(tmp_path):
    result, commands = _run_remote_deploy_fixture(tmp_path, "startup-failure")

    assert result.returncode != 0
    assert commands.count("STATE_RESTORE") == 1
    assert sum(command.startswith("COMPOSE_UP") for command in commands) == 2
    assert commands[-2:] == ["COMPOSE_UP image=sha256:previous-image", "WAIT"]


def test_remote_backup_failure_restarts_old_image_without_restore(tmp_path):
    result, commands = _run_remote_deploy_fixture(tmp_path, "backup-failure")

    assert result.returncode != 0
    assert commands.count("STATE_BACKUP") == 1
    assert commands.count("STATE_RESTORE") == 0
    assert commands.count("COMPOSE_UP image=sha256:previous-image") == 1
    assert "leaving the untouched state in place" in result.stderr


def test_remote_restore_failure_keeps_previous_image_stopped(tmp_path):
    result, commands = _run_remote_deploy_fixture(tmp_path, "restore-failure")

    assert result.returncode != 0
    assert commands.count("STATE_RESTORE") == 1
    assert commands.count("COMPOSE_UP image=sha256:previous-image") == 0
    assert "refusing to start the previous image" in result.stderr


def test_startup_wait_rejects_exit_without_historical_log_match():
    clock = Clock()

    def docker(*args):
        if args[0] == "inspect" and args[2] == "{{.State.StartedAt}}":
            return "2026-09-17T10:00:00Z\n"
        if args[0] == "inspect" and args[2] == "{{.RestartCount}}":
            return "0\n"
        if args[0] == "inspect" and args[2] == "{{.State.Status}}":
            return "exited\n"
        if args[0] == "logs":
            return "Run polling for bot from a previous start\n"
        raise AssertionError(args)

    assert not startup.wait_for_startup(
        "bot", 10, 2, docker=docker, monotonic=clock.monotonic, sleep=clock.sleep
    )
