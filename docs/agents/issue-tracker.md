# Issue tracker: GitHub (`aprudkin/tg-jira-tasks`)

Issues and PRDs for this project live in this repository's GitHub Issues tracker. Treat a request to create or file an issue as authorization to create it there.

Use `-R aprudkin/tg-jira-tasks` for explicit scoping when a command might run outside the repository. Never route an issue to another project's tracker.

## Conventions

- **Create:** `gh issue create -R aprudkin/tg-jira-tasks --title "..." --body "..."`. Use a heredoc or `--body-file` for multiline bodies.
- **Read:** `gh issue view <number> -R aprudkin/tg-jira-tasks --comments`.
- **List:** `gh issue list -R aprudkin/tg-jira-tasks --state open`.
- **Comment:** `gh issue comment <number> -R aprudkin/tg-jira-tasks --body "..."`.
- **Apply or remove labels:** `gh issue edit <number> -R aprudkin/tg-jira-tasks --add-label "..."` or `--remove-label "..."`.
- **Close:** `gh issue close <number> -R aprudkin/tg-jira-tasks --comment "..."`.

Use the triage labels documented in `triage-labels.md`. Add type, priority, or wayfinding labels only when they exist in this repository.

## Pull requests as a triage surface

`/triage` handles issues only. External pull requests are not a triage surface.

## Wayfinding operations

Keep maps and child tickets in this repository. Use GitHub sub-issues and native issue dependencies when available; otherwise use task lists and a `Blocked by: #<n>` line. Assign a ticket when claiming it, comment with the outcome, and close it when resolved.
