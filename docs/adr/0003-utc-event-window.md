# UTC window for Jira events

## Context

The sync-channel cursor is a UTC timestamp. Jira interprets an absolute JQL date without an offset in the authenticated account's configured time zone. Formatting the UTC cursor as `yyyy-MM-dd HH:mm` can therefore move the candidate boundary by several hours. Absolute JQL dates also have only minute precision, while Jira event timestamps can contain fractional seconds.

The Jira search is only the first stage of polling: it selects issues whose `updated` field may contain relevant events. Comments and changelog entries are then filtered by their own timestamps. Search and history pagination already fail closed when completeness cannot be established.

## Decision

Each poll uses the half-open UTC interval `(C, H]`:

- `C` is the channel's previous `last_check` value.
- `H` is captured immediately before the Jira request starts.
- The channel advances `last_check` to `H` only after the fetch and processing complete without an exception.

JQL uses a relative candidate lookback rather than an absolute date. Its duration is the elapsed time from `C` to query construction, rounded up to a whole minute, plus two minutes. The additional two minutes cover minute rounding and an assumed maximum two-minute difference between the bot and Jira clocks. This query is a conservative candidate filter; exact inclusion uses event timestamps converted to UTC with their fractional seconds preserved.

The event filter uses `C < event_timestamp <= H`. An event at `C` belongs to the preceding successful window, while an event at `H` belongs to the current window. Per-channel event IDs continue to handle repeated candidate issues without introducing cross-channel deduplication.

No Jira account time zone is configured or inferred. The relative duration makes the candidate query independent of UTC offsets and daylight-saving transitions. The implementation must not fall back to a timezone-less absolute JQL date.

## Indexing assumption

The two-minute JQL addition is not a replay cursor and does not claim to recover arbitrarily delayed Jira indexing. The current guarantee requires an issue's `updated` value to be visible to search during the first poll whose upper boundary follows the event timestamp. A later indexing result can fall behind `C`; persistent cursor replay and its matching event-ID retention policy belong to the delivery and recovery work tracked separately in issue #9.

If Jira returns an incomplete search or history page, polling fails without advancing `last_check`. Existing issue and history limits remain in force; an overdue channel can therefore fail closed instead of returning a partial window.

## Consequences

- UTC, positive and negative offsets, and daylight-saving transitions produce the same event inclusion result.
- Millisecond and microsecond precision from Jira timestamps is retained.
- Poll duration and Telegram delivery time cannot move the next cursor past events that occurred during the request.
- The personal channel still queries assignee, reporter, and watcher scopes. A colleague channel remains assignee-only.
- Cursors and event deduplication remain independent per channel.
- Restart behavior is unchanged: `last_check` is not persisted and restored channels establish a fresh baseline at startup. No coverage before that baseline is claimed.
