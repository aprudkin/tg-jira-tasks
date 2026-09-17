#!/usr/bin/env python3
"""Deploy an allowlisted committed snapshot; never package the live worktree."""

from __future__ import annotations

import argparse
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

DEPLOY_PATHS = (
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "uv.lock",
    "bot",
    "scripts/deploy-preflight.sh",
    "scripts/deploy-remote.sh",
    "scripts/wait_for_startup.py",
)
DEPLOY_METADATA = ".deploy-revision"
# Receiver-side protection bounds --delete to content below managed directories
# that are present in the staged snapshot (bot/ and scripts/). Every unrelated
# root entry, including runtime directories and documentation, is preserved.
RECEIVER_FILTER_RULES = (
    "R /bot/***",
    "R /scripts/***",
    "P /*",
)
DEFAULT_HOST = "sf-bot-app01"
DEFAULT_DESTINATION = "root@143.110.233.82:/root/tg-jira-tasks/"
DEFAULT_RSYNC_SHELL = (
    f"ssh -i {Path.home()}/.ssh/luk -o StrictHostKeyChecking=accept-new"
)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def stage_snapshot(repo: Path, revision: str, destination: Path) -> str:
    resolved = run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        stdout=subprocess.PIPE,
    ).stdout.strip()
    archive = destination / "snapshot.tar"
    with archive.open("wb") as output:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "archive",
                "--format=tar",
                resolved,
                *DEPLOY_PATHS,
            ],
            check=True,
            stdout=output,
        )
    with tarfile.open(archive) as snapshot:
        snapshot.extractall(destination, filter="data")
    archive.unlink()
    (destination / DEPLOY_METADATA).write_text(resolved + "\n")
    verify_payload(destination)
    return resolved


def verify_payload(staging: Path) -> None:
    allowed_roots = {path.split("/", 1)[0] for path in DEPLOY_PATHS}
    allowed_roots.add(DEPLOY_METADATA)
    actual_roots = {path.name for path in staging.iterdir()}
    unexpected = sorted(actual_roots - allowed_roots)
    if unexpected:
        raise RuntimeError(f"unexpected deployment payload roots: {', '.join(unexpected)}")

    forbidden_names = {".env", ".pi", ".memory", ".pytest_cache", "secrets"}
    forbidden = [
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.name in forbidden_names or path.name.startswith(".env.")
    ]
    if forbidden:
        raise RuntimeError("secret/runtime paths in deployment payload: " + ", ".join(forbidden))


def rsync_command(
    staging: Path,
    destination: str,
    *,
    dry_run: bool,
    rsync_shell: str = DEFAULT_RSYNC_SHELL,
) -> list[str]:
    command = [
        "rsync",
        "-avzc",
        "--delete",
        "--itemize-changes",
    ]
    if dry_run:
        command.append("--dry-run")
    for rule in RECEIVER_FILTER_RULES:
        command.extend(["--filter", rule])
    command.extend(["-e", os.path.expanduser(rsync_shell), f"{staging}/", destination])
    return command


def deploy(repo: Path, *, revision: str, dry_run: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="tg-jira-deploy-") as temporary:
        staging = Path(temporary)
        resolved = stage_snapshot(repo, revision, staging)
        print(f"Deploy snapshot: {resolved}")
        print("Allowlisted payload:")
        for path in (*DEPLOY_PATHS, DEPLOY_METADATA):
            print(f"  {path}")

        # sshai is the command transport; rsync keeps its documented raw SSH transport.
        run(["sshai", "run", "--body-file", str(staging / "scripts/deploy-preflight.sh"), DEFAULT_HOST])

        print("Rsync dry-run (protected server-only paths are omitted from deletion):")
        run(rsync_command(staging, DEFAULT_DESTINATION, dry_run=True))
        if dry_run:
            return

        run(rsync_command(staging, DEFAULT_DESTINATION, dry_run=False))
        run(["sshai", "run", "--timeout", "300", "--body-file", str(staging / "scripts/deploy-remote.sh"), DEFAULT_HOST])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    repo = Path(
        run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE).stdout.strip()
    )
    try:
        deploy(repo, revision=args.revision, dry_run=args.dry_run)
    except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f"Deployment failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
