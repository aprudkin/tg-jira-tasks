# UTC window for Jira events

## Context

A sync-channel cursor is a UTC timestamp. Jira interprets an absolute JQL date without an offset in the authenticated account's configured time zone. Formatting the UTC cursor as `yyyy-MM-dd HH:mm` can therefore move the candidate boundary by several hours. Absolute JQL dates also have only minute precision, while Jira event timestamps can contain fractional seconds.

The Jira search is only the first stage of polling: it selects issues whose `updated` field may contain relevant events. Comments and changelog entries are then filtered by their own timestamps. Search and history pagination fail closed when completeness cannot be established.

## Decision

Each channel durably stores its UTC `last_check` cursor and its own event IDs. A poll captures `C`, the durable cursor, and `H`, the UTC time immediately before the Jira request. It queries the overlapping interval `(C - 10 minutes, H]`.

The overlap covers events produced during a Jira request or slow Telegram delivery and permits recovery after restart. Event IDs are deduplicated per channel and retained for the same ten-minute replay period, measured from the durable cursor. IDs are not cleared merely because an issue enters a closed status; they age out normally, so a close/reopen sequence within replay can not resend prior events.

A poll records an event ID only after every Telegram message chunk for that event succeeds. It persists delivered IDs and, only when every event in the poll succeeds, advances the cursor to `H`. Empty successful polls also persist `H`. On any delivery, Jira, or state-write failure, the cursor remains `C`; successfully delivered IDs may be persisted, so a retry need not repeat them. A write failure fences subsequent polling until the latest in-memory snapshot is written successfully.

The state write uses atomic replacement. A process crash after Telegram accepts a message but before the state write can cause a duplicate on replay. The guarantee is therefore at-least-once delivery, not exactly-once delivery; there is no durable outbox.

JQL uses a relative candidate lookback rather than an absolute date. Its duration is the elapsed time from the lower bound to query construction, rounded up to a whole minute, plus two minutes. This is a conservative candidate filter; exact inclusion uses event timestamps converted to UTC with fractional seconds preserved.

For legacy state without a cursor, restored channels receive one startup baseline and save it before polling. This prevents an unexpected notification flood for the downtime before the upgrade, at the cost of not replaying that historical interval.

## Consequences

- UTC, positive and negative offsets, and daylight-saving transitions produce the same event inclusion result.
- Cursor, replay, retention, and deduplication remain independent per channel. The personal channel still queries assignee, reporter, and watcher scopes; a colleague channel remains assignee-only.
- Repeating `/track` for an existing channel changes its interval or marker without resetting its cursor or unread window.
- Telegram rate-limit errors honor the server-provided delay for up to two retries per chunk. Other failed checks retry with bounded exponential delays from 15 to 300 seconds and log the channel, stage, cursor bounds, and failure count without event contents or credentials.
- The ten-minute overlap does not recover Jira events indexed later than that period. Completeness still requires an issue to be visible to search during a replayed poll; incomplete Jira search or history results fail closed without advancing the cursor.
