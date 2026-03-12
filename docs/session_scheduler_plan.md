# Session Scheduler Refactor Plan

## Goal

Rework the current thread-centric, single-active-run architecture into a `workspace + session` model that better matches day-to-day Codex / OpenCode usage.

## Baseline

- Current implementation is centered on `thread_state`
- Global execution is effectively single-slot via `SerialTaskRunner`
- Reply recovery relies on mail headers, state capsule, and normalized subject fallback
- Existing adapters, prompt rendering, result capture, and mail reporting should be preserved where possible

## Target Mental Model

- `workspace`
  - Identified by `repo_path + workdir`
  - Owns multiple sessions
  - May have many sessions over time
  - May have at most one active session at a time
- `session`
  - Human-facing unit identified by the normalized task title
  - Carries the long-lived task context inside one workspace
  - Can move through queued, running, waiting, done, failed, and killed states
- `run`
  - One concrete execution attempt derived from the latest session snapshot

## Routing Rules

### New mail that is not a reply

- Parse `workspace` from `Repo` and `Workdir`
- Parse `session_name` from the mail subject after removing backend prefix
- If the workspace already contains that session name, route to the existing session
- If the workspace does not contain that session name, create a new session automatically

### Reply mail

- Must resolve to an existing session
- Resolution priority should remain stable and explicit:
  1. mail headers
  2. persisted session identifiers in state capsule
  3. normalized subject as fallback
- Reply mail must not create a new session, even if the subject text changes

## Scheduling Rules

- A session is the scheduling unit, not an individual mail
- A running session cannot start a second concurrent run for itself
- If a running session receives updates, store them as pending changes
- When the current run finishes, pending changes may enqueue the session again
- One workspace may not run two sessions at the same time
- The global scheduler should be designed for multiple worker slots
- Initial rollout may keep `max_concurrent_runs = 1`, but the architecture must not hard-code a single active run slot

## Proposed States

### Workspace

- `active_session_id`
- `queued_session_ids`
- `running_session_ids`

### Session

- `queued`
- `running`
- `waiting_user`
- `done`
- `failed`
- `killed`
- `archived`

## Phase Plan

### Phase 1: Model and Routing

- Introduce workspace/session state models and storage
- Keep execution effectively single-worker
- Route non-reply mail by `workspace + session_name`
- Auto-create sessions for new titles in the same workspace
- Ensure reply mail can only target existing sessions

### Phase 2: Queue and Scheduler

- Replace the single `_active_run` slot with queue-aware scheduling
- Enforce one active session per workspace
- Support pending updates while a session is already running
- Keep global concurrency configurable, defaulting to one worker during rollout

### Phase 3: Multiple Running Sessions

- Allow different workspaces to run at the same time
- Enforce workspace-level exclusivity under multi-worker scheduling
- Add end-to-end tests for concurrent status, kill, rerun, and waiting-user flows
- Update docs and operational guidance for the new model

## Test Gates

Each phase must stop at a clean checkpoint and pass:

- unit tests for routing, state transitions, and scheduler rules
- integration tests for app-level mail ingestion and status reporting
- full regression `pytest` for the existing codebase

No phase should continue until the previous phase is green.
