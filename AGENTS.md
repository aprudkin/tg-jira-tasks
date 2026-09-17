# Project agent instructions

## Scope and project map

- This repository is a Python 3.11 Telegram bot using aiogram 3 and Jira Cloud/Data Center integration.
- `bot/main.py` wires the dispatcher, command menu, authentication middleware, polling, and shutdown.
- Put command handling in `bot/handlers/tasks.py`, Jira queries and event retrieval in `bot/services/jira.py`, and sync-channel scheduling/state/delivery in `bot/services/notifications.py`.
- `bot/render.py` owns Telegram entity-aware formatting and message splitting; `bot/status.py` owns canonical Jira status names, ordering, and semantic groups. Reuse these owners instead of duplicating their rules.
- `bot/config.py` loads settings; `bot/middlewares/auth.py` controls Telegram user access.
- Read `README.md` for user-facing setup and commands; read relevant parts of `ARCHITECTURE.md` when changing component interactions. For a new bot command, update both the `/start` help and `BOT_COMMANDS` in `bot/main.py`.

## Domain contracts

- Before exploring domain behavior, read `CONTEXT.md` and the ADRs relevant to the change in `docs/adr/`. Use the glossary's terms in code, tests, issues, and explanations; surface a proposed ADR conflict rather than silently changing the decision.
- For sync-channel changes, read `docs/adr/0001-independent-sync-channels.md`: channels have independent intervals/cursors and deliver to one subscribed chat. Colleague channels are assignee-only; the personal channel also includes reporter/watcher scopes.
- For deduplication changes, read `docs/adr/0002-per-channel-dedup.md`: deduplication is per channel. Two notifications for one shared event from different channels are intentional.
- For polling, cursor, state, or delivery changes, read `docs/adr/0003-utc-event-window.md`: preserve the overlapping UTC window, fail-closed completeness checks, atomic state writes, and cursor advancement only after successful completion. Closed statuses do not immediately clear dedup history. Delivery is at-least-once, not exactly-once.

## Environment and commands

Run these commands from the repository root. `pyproject.toml` and `uv.lock` define dependencies; Python is constrained to `>=3.11,<3.12` and uv to `>=0.12.12,<0.13`.

| Purpose | Command | Prerequisites / effects |
| --- | --- | --- |
| Prepare local environment | `uv sync --locked --group dev` | May download Python/packages; creates the worktree-local `.venv` |
| Full tests | `task test` | Task CLI; expands to the pytest command below |
| Full tests without Task | `uv run --locked --group dev python -m pytest tests/ -v` | Locked dev environment; uv may synchronize missing dependencies |
| Focused status tests | `uv run --locked --group dev python -m pytest tests/test_status.py -v` | Same environment as full tests |
| Security checks | `task security` | Locked Bandit scan plus audit of exported runtime dependencies; the audit queries the vulnerability service |
| Build container | `task docker:build` | Docker Compose; builds image and installs locked runtime dependencies |
| Start bot stack | `task docker:up` | Docker Compose and configured `.env`; starts a real bot |

- Do not use `task init` as a prerequisite for inspection or tests: it builds and starts the bot stack.
- No repository formatter or standalone typecheck command is defined. `.github/workflows/ci.yml` runs the locked full tests, security checks, and a secret-free Docker build on pushes and pull requests; report local and remote results separately.
- For dependency changes, keep `pyproject.toml` and the uv-generated `uv.lock` consistent; do not hand-edit the lockfile or replace uv with a parallel dependency workflow.

## Testing and runtime boundaries

- The pytest suite uses mocks/fakes rather than live Jira or Telegram. `asyncio_mode = "auto"` is configured in `pyproject.toml`.
- `tests/conftest.py` supplies dummy defaults before application imports and provides `state_path` and `fake_jira` fixtures. For notification tests, inject fake collaborators and temporary state paths rather than using deployed state.
- Select tests for the affected behavior; `task test` is the documented full-suite check. Report which checks actually ran and any limitations. Instruction-only changes do not require local application startup, but still require deployment under the delivery rule below.
- Configuration is instantiated on import, while the Jira client connects lazily. Do not import/run application modules merely to discover settings or commands.
- Before authorized deployment work, read `DEPLOY.md` and the `deploy` task in `Taskfile.yml`. Deployment uses `rsync --delete`, rebuilds/restarts production, and can remove remote backup files. Even `task deploy:dry` contacts the production host; neither is a local validation command.
- The notification state is stored in the Compose `bot_data` volume at `/app/data/sync_state.json`. Preserve it during unrelated changes. Do not reproduce documentation commands that print real environment credentials.

## Required delivery: commit and deploy

- After every project artifact change, including documentation-only and agent-instruction changes, run the applicable checks, create a Git commit, and deploy to production. This is the project's standing authorization for routine commits and the documented deployment; do not ask for the same authorization again. An explicit user restriction for the current task takes precedence.
- Commit only the task's changes, using Conventional Commits and the applicable issue reference. Do not include unrelated work or secrets. This rule does not require or authorize a Git push.
- Before deployment, read `DEPLOY.md` and the `deploy` task in `Taskfile.yml`; use that deployment workflow. Confirm that the deployment payload contains only intended project files. Preserve the production `.env`, notification-state volume, and unrelated services; do not include local credentials or agent runtime data. If the workflow would exceed these boundaries, resolve the blocker before deploying.
- Verify the deployed container is running and its new startup logs confirm polling started. Report the commit ID, checks performed, and actual deployment result. Do not mark the task done or close its issue until commit, deployment, and startup verification succeed; record any blocker instead of claiming completion.
- Read-only explanations and investigations without project artifact changes do not require an empty commit or deployment.

## Project LSP

- `.pi/settings.json` activates the existing pi-lsp client only for this project; `.pi/lsp.json` configures Pyright with `.venv/bin/python`. These files reuse machine-specific installed paths; they do not install tools on another machine.
- Start Pi from the repository root. After context/configuration changes, use `/reload`; accept project trust and the separate LSP configuration trust prompt yourself when shown. A changed LSP configuration requires renewed trust.
- For this client's Python diagnostics, successful Pi `write`/`edit` events update the server and collect results for 2.5 seconds. `lsp_diagnostics` only reads a snapshot; an empty or stale snapshot does not establish a fresh clean check, and shell edits do not trigger the same hook.
- LSP supplements the pytest checks above. If the client, server, or environment is unavailable, use source search and applicable CLI checks and report the limitation; do not install or globally activate replacements automatically.

## Issue tracking

- For this repository, read `docs/agents/issue-tracker.json` for the GitHub target, project label, and approved areas. Apply the global task-classification, lifecycle, journal, and commit-reference rules.
- Older triage/wayfinding notes in `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md` describe auxiliary workflows, not replacements for the configured type/project/area/lifecycle labels. Do not infer permission to create or migrate labels from those examples.
