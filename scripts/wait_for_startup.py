#!/usr/bin/env python3
"""Wait for the current container start to reach aiogram polling."""

from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable

POLLING_MARKER = "Run polling for bot"


def _docker(*args: str) -> str:
    # Docker returns container stdout/stderr as separate client streams. Python
    # logging and aiogram normally write the polling marker to stderr.
    stderr = subprocess.STDOUT if args and args[0] == "logs" else subprocess.DEVNULL
    result = subprocess.run(
        ["docker", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
    )
    return result.stdout


def wait_for_startup(
    container: str,
    timeout: float,
    interval: float = 2.0,
    *,
    docker: Callable[..., str] = _docker,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    started_at = docker("inspect", "--format", "{{.State.StartedAt}}", container).strip()
    initial_restarts = int(
        docker("inspect", "--format", "{{.RestartCount}}", container).strip()
    )
    if initial_restarts != 0:
        return False
    deadline = monotonic() + timeout

    while monotonic() < deadline:
        status = docker("inspect", "--format", "{{.State.Status}}", container).strip()
        restarts = int(
            docker("inspect", "--format", "{{.RestartCount}}", container).strip()
        )
        if status != "running" or restarts != initial_restarts:
            return False

        logs = docker("logs", "--since", started_at, container)
        if POLLING_MARKER in logs:
            return True
        sleep(interval)

    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("container")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()

    try:
        ready = wait_for_startup(args.container, args.timeout, args.interval)
    except (OSError, subprocess.CalledProcessError, ValueError):
        ready = False

    if ready:
        print(f"{args.container}: current start reached polling")
        return 0
    print(f"{args.container}: startup failed or timed out before polling")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
