# Session Scheduler Status And Remaining Plan

> Archived legacy source file.
> Current canonical doc: `docs/current/session_scheduler_status.md`

## Status Snapshot

As of 2026-03-14, the scheduler refactor is no longer just a proposal. The codebase has already landed a hybrid model:

- run artifacts still persist under `tasks/thread_xxx/`
- workspace/session indexes persist under `tasks/_scheduler/workspaces/<workspace_id>/`
- `WorkspaceState` and `SessionState` are derived from and kept in sync with `ThreadState`
- the background runner enforces one active session per workspace
- different workspaces can run concurrently up to `max_concurrent_runs`
- follow-up work for a running session is queued on that same session
- accepted and queued work can be recovered after runner restart

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
- restarting the runner requeues persisted `accepted` work
- restarting the runner promotes queued follow-up work if the previous run was interrupted
- restarting the runner marks an orphaned persisted `running` task as failed when no safe queued follow-up exists

### Mail-layer behavior already using this model

- `/sessions` lists sessions in the current workspace
- reply mail continues an existing session when explicit thread/session clues are available
- `/new` creates a fresh session from an existing reply chain
- native backend session ids are persisted and reused for reply continuation and question-answer recovery

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

### User-visible pause protocol

The status enums reserve `paused`, but there is still no mail protocol or slash command for pausing and resuming.

### Fixed real-mailbox acceptance

The real mailbox validation trail still needs to be upgraded so that these paths are covered as fixed acceptance items:

- `[QUESTION] -> ANSWER -> DONE` with a real backend
- real-backend `KILL` in the normal mailbox loop

## Remaining Recommended Work

### Step 1: Mail routing alignment

- decide whether new non-reply mail should reuse an existing session when `workspace + normalized title` matches exactly
- if yes, update the app mail-ingest path and add collision tests for same-title mail in one workspace

### Step 2: Session control UX

- decide whether `/sessions` should remain read-only or evolve into a session-switch workflow
- if switching is added, make the routing explicit and avoid hidden title-based guessing

### Step 3: Runtime protocol hardening

- add a concrete `paused` protocol
- lock down fixed real-mailbox acceptance for question/answer and kill
- keep the existing workspace exclusivity and restart-recovery guarantees green while extending behavior

## Test Gates

Any follow-up scheduler work should continue to require:

- unit tests for workspace/session state transitions
- app-level tests for mail routing and status reporting
- runner tests for queueing, recovery, and concurrency
- full regression `.\.venv\Scripts\python.exe -m pytest`
