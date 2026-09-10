# Pi-specific project instructions

## Validation

- Run the full local test suite with `task test`. Tests inject dummy environment values and do not require real credentials.
- Before changing sync-channel or notification deduplication behavior, read `CONTEXT.md` and `docs/adr/0001-independent-sync-channels.md` plus `docs/adr/0002-per-channel-dedup.md`. Preserve colleague-channel assignee-only scope and per-channel deduplication unless an ADR is intentionally superseded.

## Issue creation

- A request to create or file an issue authorizes creating it in GitHub Issues at `github.com/aprudkin/tg-jira-tasks`. Always target that repository explicitly; if Issues is disabled, report the operation as blocked rather than using another tracker.
