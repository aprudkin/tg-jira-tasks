#!/usr/bin/env bash
set -euo pipefail

remote=/srv/tg-jira-bot
export COMPOSE_FILE=docker-compose.nc.yml
container=tg-jira-bot
volume=tg_jira_bot_data

fail() {
  printf 'Preflight failed: %s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null || fail 'docker is unavailable'
command -v python3 >/dev/null || fail 'python3 is unavailable'
test -d "$remote" || fail 'existing deployment directory is missing'
test -f "$remote/runtime.env" || fail 'production environment file is missing'
test -f "$remote/docker-compose.nc.yml" \
  || fail 'existing Compose installation is missing; task deploy does not bootstrap a new host'

# Do not render Compose configuration: it can contain environment values.
(cd "$remote" && docker compose config --quiet >/dev/null 2>&1) \
  || fail 'existing Compose configuration is invalid'

mapfile -t service_containers < <(cd "$remote" && docker compose ps -aq bot)
((${#service_containers[@]} <= 1)) || fail 'more than one bot service container exists'

if docker inspect "$container" >/dev/null 2>&1; then
  mounts=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' "$container")
  [[ "$mounts" == "$volume" ]] || fail 'bot state volume mount is not the expected named volume'
fi

printf 'Preflight passed: one bot service at most; environment exists; state mount is bounded\n'
