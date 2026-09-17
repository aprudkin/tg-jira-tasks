"""Exact, content-opaque backup and restore for deployment rollback."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

STATE_BACKUP = "sync_state.json"
STATE_ABSENT = "state.absent"


def backup(state_file: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_file = backup_dir / STATE_BACKUP
    absent_file = backup_dir / STATE_ABSENT
    if state_file.is_file():
        shutil.copyfile(state_file, backup_file)
        os.chmod(backup_file, 0o600)
        absent_file.unlink(missing_ok=True)
    else:
        backup_file.unlink(missing_ok=True)
        absent_file.touch(mode=0o600, exist_ok=True)


def restore(state_file: Path, backup_dir: Path) -> None:
    backup_file = backup_dir / STATE_BACKUP
    absent_file = backup_dir / STATE_ABSENT
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.parent / f".{state_file.name}.rollback"
    temporary.unlink(missing_ok=True)

    if absent_file.is_file() and not backup_file.exists():
        state_file.unlink(missing_ok=True)
    elif backup_file.is_file() and not absent_file.exists():
        shutil.copyfile(backup_file, temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(state_file)
    else:
        raise RuntimeError("rollback snapshot is incomplete or ambiguous")

    (state_file.parent / f"{state_file.name}.tmp").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("state_file", type=Path)
    parser.add_argument("backup_dir", type=Path)
    args = parser.parse_args()

    try:
        if args.operation == "backup":
            backup(args.state_file, args.backup_dir)
        else:
            restore(args.state_file, args.backup_dir)
    except (OSError, RuntimeError):
        print(f"State {args.operation} failed")
        return 1
    print(f"State {args.operation} completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
