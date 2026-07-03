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

**Marker emoji**:
The emoji that tags a channel's notifications so a reader can attribute an event to its tracked user within the shared chat. Distinct from the event-type icon.
_Avoid_: Icon (reserved for event-type icon), badge, tag

**Event-type icon**:
The emoji that signals *what kind* of change occurred — 🆕 created, 💬 comment, 🔄 status change, 👤 assignment. Orthogonal to the marker emoji, which signals *whose* channel surfaced it.
_Avoid_: Emoji (ambiguous — could mean marker emoji)

**Event**:
A single Jira change surfaced to the subscribed chat: an issue created, a comment added, a status change, or an assignment. Deduplicated per channel by a stable id.
_Avoid_: Notification (an event is the source; the notification is its rendered message), update, change
