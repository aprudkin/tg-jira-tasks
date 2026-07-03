# tg-jira-tasks

A Telegram bot that reports a user's Jira activity and pushes notifications about changes to the issues they care about.

## Language

**Subscribed chat**:
The single Telegram chat that receives notifications. The bot serves one chat at a time; every sync channel delivers here.
_Avoid_: Subscriber, recipient

**Tracked user**:
A Jira identity whose issue activity is monitored. The bot's own Jira account ("me", `currentUser()`) is a tracked user; a colleague monitored by their Jira username is another.
_Avoid_: Colleague, employee, watchee (a colleague is one *kind* of tracked user, not the concept itself)

**Sync channel** (channel):
An independent monitoring stream for exactly one tracked user. Each channel carries its own check interval, its own last-checked point, its own marker emoji, and its own on/off state. All channels deliver to the same subscribed chat. "My" sync is one channel; each tracked colleague is another.
_Avoid_: Subscription (that named the old single-user model), watch, feed

**Personal channel**:
The sync channel for the bot's own Jira account (`currentUser()`, "me"). Carries no marker emoji — in a single-owner chat its events need no attribution. Managed via `/sync` and `/unsync`.
_Avoid_: Subscription, my sync

**Colleague channel**:
A sync channel for a tracked user other than "me", matched by Jira username and tagged with a marker emoji. Managed via `/track` and `/untrack`. One *kind* of sync channel (the counterpart to the personal channel), not a synonym for tracked user.
_Avoid_: Watch, follow

**Marker emoji**:
The emoji that tags a channel's notifications so a reader can attribute an event to its tracked user within the shared chat. Distinct from the event-type icon.
_Avoid_: Icon (reserved for event-type icon), badge, tag

**Event-type icon**:
The emoji that signals *what kind* of change occurred — 🆕 created, 💬 comment, 🔄 status change, 👤 assignment. Orthogonal to the marker emoji, which signals *whose* channel surfaced it.
_Avoid_: Emoji (ambiguous — could mean marker emoji)

**Event**:
A single Jira change surfaced to the subscribed chat: an issue created, a comment added, a status change, or an assignment. Deduplicated per channel by a stable id.
_Avoid_: Notification (an event is the source; the notification is its rendered message), update, change

**Muted author**:
A Jira identity whose authored events are delivered to the subscribed chat silently (no Telegram sound). Cross-cutting: applied across every sync channel and matched by the event's author, independent of which channel surfaced the event.
_Avoid_: Silent user, muted user (both overload "user" — this concerns the event's author, not the tracked user)

**Status**:
A Jira workflow state of an issue (`In Progress`, `On Hold`, `Resolved`, …). The canonical status names, their display order, and the semantic groups below live in one owner; every query and every grouping refers to it, so a status name never appears twice.
_Avoid_: State, stage

**Backlog group**:
The statuses that mean *not started* — `To Do`, `Backlog`, `Open`. Queried together as one bucket.
_Avoid_: Todo, unstarted

**Waiting group**:
The statuses that mean *blocked on a decision* — `Discussion`, `On Hold`. Queried and displayed together.
_Avoid_: Blocked, paused, hold

**Closed group**:
The statuses that mean *finished* — `Done`, `Closed`, `Resolved`. Reaching one lets a sync channel clear that issue's event-dedup history.
_Avoid_: naming it after a single member (Done / Closed / Resolved)
