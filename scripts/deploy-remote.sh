#!/usr/bin/env bash
set -Eeuo pipefail

remote=${DEPLOY_REMOTE:-/root/tg-jira-tasks}
container=tg-jira-bot
volume=tg-jira-tasks_bot_data
wait_timeout=${DEPLOY_WAIT_TIMEOUT:-90}
new_image=''
old_image=''
backup_dir=''
changed=0
snapshot_ready=0

restore_state() {
  docker run --rm --user 0 \
    -v "$volume:/data" -v "$backup_dir:/backup:ro" \
    --entrypoint python "$new_image" \
    -m bot.state_snapshot restore /data/sync_state.json /backup \
    || return 1
  docker run --rm --user 0 -v "$volume:/data" \
    --entrypoint sh "$new_image" -ceu 'chown -R 10001:10001 /data'
}

rollback() {
  trap - ERR
  set +e
  echo 'Deployment failed after the bot was stopped; attempting rollback' >&2
  docker rm -f "$container" >/dev/null 2>&1 || true

  restore_status=0
  if ((snapshot_ready)); then
    restore_state || restore_status=$?
  else
    # Backup reads the volume through a read-only mount. If it failed, production
    # state was not modified yet and must be left untouched.
    echo 'No complete rollback snapshot; leaving the untouched state in place' >&2
  fi

  start_status=1
  if ((restore_status != 0)); then
    echo 'State restore failed; refusing to start the previous image on uncertain state' >&2
  elif [[ -n "$old_image" ]]; then
    BOT_IMAGE="$old_image" docker compose up -d --no-deps --no-build --force-recreate bot
    start_status=$?
    if ((start_status == 0)); then
      python3 scripts/wait_for_startup.py "$container" --timeout "$wait_timeout"
      start_status=$?
    fi
  else
    echo 'No previous bot image existed; automatic image rollback is unavailable' >&2
  fi

  if ((restore_status != 0 || start_status != 0)); then
    echo 'Automatic rollback did not complete; the bot remains stopped or unhealthy' >&2
  else
    echo 'Previous image and pre-deploy state restored; duplicate events remain possible under at-least-once delivery' >&2
  fi
  exit 1
}

on_error() {
  if ((changed)); then
    rollback
  fi
  exit 1
}

main() {
  cd "$remote"
  revision=$(tr -d '\n' < .deploy-revision)
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] \
    || { echo 'Invalid deployment revision metadata' >&2; exit 1; }
  new_image="tg-jira-tasks-bot:${revision}"
  if docker inspect "$container" >/dev/null 2>&1; then
    old_image=$(docker inspect --format '{{.Image}}' "$container")
  fi

  BOT_IMAGE="$new_image" docker compose build bot
  BOT_IMAGE="$new_image" docker compose run --rm --no-deps \
    --entrypoint python bot -m bot.config_check

  backup_dir="$remote/backups/${revision}-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$backup_dir"
  chmod 700 "$remote/backups" "$backup_dir"
  trap on_error ERR

  # Stop exactly the bot service before taking the rollback snapshot. Other services are untouched.
  docker compose stop bot
  changed=1

  docker run --rm --user 0 \
    -v "$volume:/data:ro" -v "$backup_dir:/backup" \
    --entrypoint python "$new_image" \
    -m bot.state_snapshot backup /data/sync_state.json /backup
  snapshot_ready=1

  # Validate only a temporary copy: compatibility checks never migrate production state.
  docker run --rm --user 0 --env-file .env \
    -e STATE_FILE=/tmp/state/sync_state.json \
    -v "$volume:/source:ro" --entrypoint sh "$new_image" -ceu '
      mkdir -p /tmp/state
      if test -f /source/sync_state.json; then
        cp /source/sync_state.json /tmp/state/sync_state.json
      fi
      python -m bot.state_check
    '

  # One-time migration is constrained to the dedicated bot_data volume.
  docker run --rm --user 0 -v "$volume:/app/data" \
    --entrypoint sh "$new_image" -ceu 'chown -R 10001:10001 /app/data'

  # Prove the runtime UID can read state and atomically create files in its directory.
  docker run --rm --user 10001:10001 -v "$volume:/app/data" \
    --entrypoint sh "$new_image" -ceu '
      test ! -f /app/data/sync_state.json || test -r /app/data/sync_state.json
      probe=$(mktemp /app/data/.deploy-write.XXXXXX)
      rm -f "$probe"
    '

  BOT_IMAGE="$new_image" docker compose up -d --no-deps --no-build --force-recreate bot
  python3 scripts/wait_for_startup.py "$container" --timeout "$wait_timeout"

  mapfile -t running < <(docker ps --filter "name=^/${container}$" --format '{{.ID}}')
  ((${#running[@]} == 1)) || { echo 'Singleton polling check failed' >&2; false; }
  runtime_user=$(docker inspect --format '{{.Config.User}}' "$container")
  [[ "$runtime_user" == '10001:10001' ]] \
    || { echo 'Non-root runtime check failed' >&2; false; }

  trap - ERR
  printf 'Deployment succeeded: committed image is polling as one container; rollback state is %s\n' "$backup_dir"
}

if [[ ${DEPLOY_REMOTE_LIB_ONLY:-0} != 1 ]]; then
  main "$@"
fi
