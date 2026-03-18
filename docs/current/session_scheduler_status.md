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
- the background runner enforces one active session per workspace
- different workspaces can run concurrently up to `max_concurrent_runs`
- follow-up work for a running session is queued on that same session
- accepted and queued work can be recovered after runner restart, including automatic status-mail callbacks

What is still incomplete is the mail-routing layer and the user-facing control surface around those scheduler primitives.

## What Has Landed

### Storage and identity

- `workspace` is identified by `repo_path + workdir`
- `session` metadata is stored separately from the thread directory tree
- session and workspace indexes are rebuilt/synced when `thread_state.json` changes

### Scheduling rules

- one workspace cannot run two sessions at the same time
- different workspaces may run concurrently
- a running session can hold one queued follow-up snapshot
- restarting the runner requeues persisted `accepted` work and preserves automatic `[RUNNING]` / terminal mail callbacks
- restarting the runner promotes queued follow-up work if the previous run was interrupted and preserves automatic status-mail callbacks
- restarting the runner marks an orphaned persisted `running` task as failed when no safe queued follow-up exists

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
- reply mail continues an existing session when explicit thread/session clues are available
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

The current command surface is still thread-local.

Current reality:

- `/sessions` is informational
- continuing a different session still requires replying to that session's latest status mail
- explicit command-side targeting/switching is not implemented

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

- decide whether `/sessions` should remain read-only or evolve into a session-switch workflow
- if switching is added, make the routing explicit and avoid hidden title-based guessing

### Step 3: Runtime protocol hardening

- keep the fixed real-mailbox acceptance paths green while extending behavior
- keep the existing workspace exclusivity and restart-recovery guarantees green while extending behavior

## Test Gates

Any follow-up scheduler work should continue to require:

- unit tests for workspace/session state transitions
- app-level tests for mail routing and status reporting
- runner tests for queueing, recovery, and concurrency
- full regression `.\.venv\Scripts\python.exe -m pytest`
