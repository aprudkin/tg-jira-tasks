"""Rollback snapshots preserve state bytes and the no-state case."""

from bot.state_snapshot import backup, restore


def test_rollback_restores_exact_predeploy_state(tmp_path):
    state_file = tmp_path / "data" / "sync_state.json"
    backup_dir = tmp_path / "backups" / "revision"
    state_file.parent.mkdir()
    original = b'{"schema_version":2,"cursor":"before"}\n'
    state_file.write_bytes(original)

    backup(state_file, backup_dir)
    state_file.write_bytes(b'{"schema_version":999,"cursor":"after"}\n')
    (state_file.parent / "sync_state.json.tmp").write_text("partial")
    restore(state_file, backup_dir)

    assert state_file.read_bytes() == original
    assert not (state_file.parent / "sync_state.json.tmp").exists()


def test_rollback_restores_absence_of_state(tmp_path):
    state_file = tmp_path / "data" / "sync_state.json"
    backup_dir = tmp_path / "backups" / "revision"

    backup(state_file, backup_dir)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("created by failed image")
    restore(state_file, backup_dir)

    assert not state_file.exists()
