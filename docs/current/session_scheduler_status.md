# Session Scheduler Status And Remaining Plan

> 文档层级：Layer 1（当前仓库状态与协议边界）
>
> 当前 canonical 路径：`docs/current/session_scheduler_status.md`
> 已归档旧来源文件：`docs/archive/session_scheduler_plan_legacy.md`

## Status Snapshot

As of 2026-03-14, the scheduler refactor is no longer just a proposal. The codebase has already landed a hybrid model:

- run artifacts still persist under `tasks/thread_xxx/`
- workspace/session indexes persist under `tasks/_scheduler/workspaces/<workspace_id>/`
- `WorkspaceState` and `SessionState` are derived from and kept in sync with `ThreadState`
- the active working set is capped separately from running concurrency
- active-session caps are now `max_active_sessions` globally plus `max_active_sessions_per_workspace` for one workspace
- running concurrency caps are now `max_running_sessions` globally plus `max_running_sessions_per_workspace` for one workspace
- follow-up work for a running session is queued on that same session
- accepted and queued work can be recovered after runner restart, including automatic status-mail callbacks

What is still incomplete is the non-reply routing policy and any broader cross-workspace control surface around those scheduler primitives.

## What Has Landed

### Storage and identity

- `workspace` is identified by `repo_path + workdir`
- `session` metadata is stored separately from the thread directory tree
- workspace state now persists `active_session_ids` plus compatibility `active_session_id`
- session and workspace indexes are rebuilt/synced when `thread_state.json` changes

### Scheduling rules

- one workspace may now hold multiple `active` sessions up to `max_active_sessions_per_workspace`; when omitted it inherits the global `max_active_sessions` cap
- one workspace may now run multiple sessions concurrently up to `max_running_sessions_per_workspace` (default `2`)
- different workspaces may stay `active` until the global `max_active_sessions` cap is reached
- different workspaces may run concurrently until the global `max_running_sessions` cap is reached
- a running session can hold one queued follow-up snapshot
- restarting the runner requeues persisted `accepted` work and preserves automatic `[RUNNING]` / terminal mail callbacks
- restarting the runner promotes queued follow-up work if the previous run was interrupted and preserves automatic status-mail callbacks
- restarting the runner marks an orphaned persisted `running` task as failed when no safe queued follow-up exists
- `max_active_sessions` is now only the global active working-set cap
- `max_active_sessions_per_workspace` is now the same-workspace active working-set cap
- `max_running_sessions` is now the global running concurrency cap
- `max_running_sessions_per_workspace` is now the same-workspace running concurrency cap

### Health visibility

- thread/session state now persists `last_progress_at` in addition to `updated_at`
- `mail_runner.observe` derives `normal`, `stale`, `suspected_stuck`, and `orphaned`
- the first-round stale/stuck threshold is fixed at `300s`
- `running` with no progress beyond that threshold is reported as `suspected_stuck`
- `queued`, `paused`, and `waiting_user` beyond that threshold are reported as `stale`
- active queued/running work with no live host is reported as `orphaned`
- `codex + sdk` live stream timestamps participate in the progress calculation when newer than persisted state

### Mail-layer behavior already using this model

- `/sessions` lists sessions in the current workspace
- `/sessions` now also gives copyable targeted command hints for the current workspace
- reply mail continues an existing session when explicit thread/session clues are available
- explicit same-workspace targeting from commands is now available for `/status <session_id>`, `/last <session_id>`, `/continue <session_id>`, `/pause <session_id>`, `/resume <session_id>`, `/end <session_id>`, and `/kill <session_id>`
- targeted command results continue on the target session's own mail chain instead of the invoking thread
- `/new` creates a fresh session from an existing reply chain
- `/end` explicitly removes a non-running thread/session from the active working set without rewriting its last run result
- native backend session ids are persisted and reused for reply continuation and question-answer recovery
- recovered accepted work keeps the existing reply chain, so restart-resumed runs still emit automatic `[RUNNING]`, `[DONE]`, `[FAILED]`, `[QUESTION]`, or `[PAUSED]` mails to the user mailbox
- live mailbox cleanup now only replaces older progress mails; `[QUESTION]`, `[PAUSED]`, `[DONE]`, `[FAILED]`, and `[KILLED]` remain as durable action-required or receipt mail

### User-visible lifecycle protocol

The mail control plane now also has a lightweight lifecycle axis:

- thread and session state persist `lifecycle: active|ended`
- `/end` is available for non-running threads/sessions and only changes lifecycle; it does not overwrite `done`, `failed`, `killed`, or `paused`
- `/resume` can reactivate an ended thread back into `active`
- if an ended thread is also paused or waiting for answers, the same thread is reactivated instead of starting a fresh thread
- `accepted/running` work still requires waiting or `/kill` before `/end`

### User-visible pause protocol

The mail control plane now has a concrete paused protocol:

- `/pause` is available for non-running threads/sessions and moves thread + session state to `paused`
- `paused` stores `paused_from_status` so the mail layer can still distinguish whether the session was paused from `done`, `failed`, `killed`, or `awaiting_user_input`
- plain reply does not implicitly resume a paused session
- `/resume` is the only explicit unpause command:
  - if the paused session still has a pending question set, `/resume` without an answer restores `[QUESTION]`
  - if the paused session includes answers, it continues through the normal answer/resume path
  - if there is no pending question set, `/resume` restores the normal continuation/recovery semantics
- `/pause` does not suspend an already-running CLI process; `running/accepted` work still requires waiting or `/kill`

## What Has Not Landed Yet

### New non-reply routing

The original plan proposed routing new non-reply mail by `workspace + session_name`.

Current reality:

- every non-reply new task mail still creates a fresh `thread/session`
- matching an existing session by title is not implemented yet

### Session targeting from commands

The first round of explicit command-side targeting has landed, but only inside the current workspace.

Current reality:

- `/sessions` remains the discovery entrypoint for the current workspace
- users can now target another session in that same workspace with explicit commands such as `/status <session_id>`, `/last <session_id>`, and `/continue <session_id>`
- targeted replies continue on the target session's own mail chain
- cross-workspace switching is still not implemented

### Fixed real-mailbox acceptance

The real mailbox validation trail now includes fixed acceptance coverage for these paths:

- `[QUESTION] -> ANSWER -> DONE` with a real backend via `scripts/live_smoke_mail_question_answer.py`
- real-backend `KILL` in the normal mailbox loop via `scripts/live_smoke_mail_kill.py`

Current evidence:

- `_tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74`
- `_tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5`

## Remaining Recommended Work

### Step 1: Mail routing alignment

- decide whether new non-reply mail should reuse an existing session when `workspace + normalized title` matches exactly
- if yes, update the app mail-ingest path and add collision tests for same-title mail in one workspace

### Step 2: Session control UX

- refine Android/Desktop UX around the new explicit same-workspace targeting flow
- if broader switching is added later, keep the routing explicit and avoid hidden title-based guessing

### Step 3: Runtime protocol hardening

- keep the fixed real-mailbox acceptance paths green while extending behavior
- keep the existing same-thread follow-up queueing, workspace concurrency caps, and restart-recovery guarantees green while extending behavior

## Test Gates

Any follow-up scheduler work should continue to require:

- unit tests for workspace/session state transitions
- app-level tests for mail routing and status reporting
- runner tests for queueing, recovery, and concurrency
- full regression `.\.venv\Scripts\python.exe -m pytest`
