# Per-channel deduplication (double-notify on shared issues)

## Context

"My tickets" and "a colleague's tickets" overlap: on an issue where I am reporter and the colleague is assignee, both channels match the same comment or status change. Channels run on independent intervals, so *which* channel observes an event first is nondeterministic in time.

## Decision

Deduplicate **per channel**: each channel keeps its own set of processed event ids. A shared-issue event therefore produces **two** notifications — one per channel — each carrying that channel's own marker emoji. There is no cross-channel deduplication.

## Considered options

- **Global / first-wins dedup** (notify once). Rejected: the surviving notification's marker emoji would flip depending on timing, and it couples channels together, breaking the independence established in ADR-0001.

## Consequences

- The same comment on a shared issue arrives twice. This is intentional — they are two genuinely different reasons to be notified ("event on an issue you reported" vs "event on your report's ticket"). A maintainer must not collapse this as if it were a bug.
