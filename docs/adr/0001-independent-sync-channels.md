# Independent per-user sync channels

## Context

The notification service tracked a single Jira user (`currentUser()`) on one interval, with one deduplication set and one subscription. We need to also monitor arbitrary colleagues, each on its own check interval, each visually distinguished within the same chat.

## Decision

Model notifications as N independent **sync channels**. Each channel monitors exactly one Jira user with its own check interval, its own last-checked point, its own marker emoji, and its own deduplication set. The personal channel (`__me__`) is just one channel among them; every channel delivers to the single subscribed chat.

## Considered options

- **A single widened JQL** (`assignee = me OR assignee = colleague …`) with per-issue owner tagging. Rejected: the user wants *separate check intervals* per colleague, which a single loop/query cannot express; and a widened query cannot attribute a shared issue to a specific channel/marker.

## Consequences

- Colleague channels query `assignee = "X"` **only** — a colleague's "tickets" means what they are working on, not what they reported or watch. This is deliberately asymmetric with the personal channel (`assignee OR reporter OR watcher = currentUser()`); do not "fix" it by adding `reporter`/`watcher` to colleague channels. It also sidesteps the `watcher = <other user>` permission restriction (querying another user's watched issues needs "Manage Watchers").
- State schema becomes `channels: {user → {interval_minutes, emoji, processed_events}}`; the old flat schema migrates on load into the `__me__` channel so a running deployment keeps its subscription and dedup.
