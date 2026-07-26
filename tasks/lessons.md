# Lessons

## 2026-03-02 — Always create aimem issue after completing work
- **Failure mode:** Completed a refactor (remove redundant `parse_mode`), committed, and reported results — but skipped creating an aimem issue as required by DoD in global CLAUDE.md.
- **Detection signal:** User had to explicitly ask "why wasn't this done?" after the work was complete.
- **Prevention rule:** After any commit, before reporting results to the user, check DoD: create aimem issue → add to project board → close with final checkpoint. This applies even for small refactors.

## 2026-06-26 — Verify where a service runs before editing config to change its behavior
- **Failure mode:** Edited the local `.env` (token/owner) to fix a misbehaving bot, but the bot runs in Docker on a remote server (143.110.233.82) and the deploy `rsync` excludes `.env` — so the local edit was inert and changed nothing in production. The real cause was a revoked token on the *server's* config (`TelegramUnauthorizedError` loop).
- **Detection signal:** Change "doesn't take effect"; the container hasn't restarted recently (`docker ps` shows long uptime); the deploy mechanism (rsync/CI) excludes the edited file; the service runs on a remote host (see `DEPLOY.md`).
- **Prevention rule:** Before editing config to change a running service's behavior, confirm the deployment target and whether the edited file actually reaches it. For this project, edit `/root/tg-jira-tasks/.env` on the server and recreate with `docker compose down && up -d` (not `restart`, which doesn't reload `env_file`).

## 2026-06-26 — "Failed to enable notifications." means the single subscriber slot is taken
- **Failure mode:** Misreading `/sync` replying "Failed to enable notifications." as a generic bug, when the notification service is single-subscriber and the slot is already held by a different `chat_id`.
- **Detection signal:** `/sync` returns "Failed to enable notifications." while `sync_state.json` has `chat_id` set to another user; `subscribe()` (`bot/services/notifications.py`) returns `False` only when `_chat_id is not None`.
- **Prevention rule:** The bot supports one subscriber at a time. To switch subscriber, clear the existing subscription first (`/unsync` from the current owner, or stop container → remove `/app/data/sync_state.json` from the `bot_data` volume → start) before the new owner runs `/sync`.

## 2026-07-03 — Serial→parallel loop refactor races on shared persistence
- **Failure mode:** Splitting one background loop into N concurrent per-channel loops made the shared `_save_state` racy — common `sync_state.json.tmp` path (concurrent writes → corrupt JSON → lost subscription on next load) and cross-thread dict iteration in `to_thread` (`RuntimeError: dictionary changed size`). The single loop had implicitly serialized all writes, hiding the need for locking.
- **Detection signal:** Every unit test drove channels sequentially and mocked `get_events_since`/`check_now`, so the real `_channel_loop`, `start`/`stop`, and concurrent saves never executed. The advisor caught it, not the suite.
- **Prevention rule:** When parallelizing a previously-serial loop, audit every shared-state write for concurrency — build the serializable snapshot in the event-loop thread and serialize the actual write with an `asyncio.Lock` (or per-write unique tmp name). Add ≥1 integration test that runs the real loops concurrently and asserts persisted state stays valid and complete.

## 2026-07-04 — Verify code landed on the server before rebuilding
- **Failure mode:** The deploy `rsync` auto-backgrounded (Bash tool) and silently didn't sync; the next `docker compose up -d --build` would have rebuilt the *old* code with no error.
- **Detection signal:** rsync produced empty/no output and its background output file was empty; the server still held the pre-change code.
- **Prevention rule:** Run the deploy rsync in the foreground (with a timeout). Before `docker compose up --build`, SSH-grep the server for a symbol that exists only in the new code (a new file/function). Don't infer rsync success when it may have been backgrounded.

## 2026-07-04 — `grep -c` breaks `&&` chains on zero matches
- **Failure mode:** Chained `grep -c PATTERN file && docker compose up --build`; the grep found 0 matches, printed `0`, but exited non-zero (grep's "no match" status), short-circuiting the `&&` so the rebuild never ran — with no visible error.
- **Detection signal:** A compound `&&` command stops right after a `grep -c` that legitimately returned 0; the intended follow-up action didn't happen.
- **Prevention rule:** Never gate an action on `grep -c` (or bare `grep`) in an `&&` chain. Separate verification from action (run them as distinct commands), or append `|| true` to the grep when only its printed count matters.

## 2026-07-04 — Relocating side-effecting calls can reorder user-visible output
- **Failure mode:** Moving the enable/track orchestration into `NotificationService` methods also pulled `check_now` inside them. Since the service method returns *before* the handler renders its reply, event notifications from `check_now` would land *before* the "Checking…/Слежу за…" confirmation — a visible reorder, despite "behavior-preserving" being the goal.
- **Detection signal:** A refactor moves a call that emits output (sends messages / writes) relative to another output the user sees; the two are now sequenced by a different code path.
- **Prevention rule:** Keep UI-sequencing side effects on the caller side, ordered explicitly around the reply. Service methods should do decision + state mutation and return an outcome; the handler renders, then triggers the side effect. When "preserving behavior," trace observable *ordering*, not just final state.
