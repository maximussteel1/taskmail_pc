# P8 Session Targeting And Routing UX Plan

## Status

- Date: 2026-03-18
- Scope: explicit session targeting and routing UX for the current repository
- Goal: make session selection explicit without breaking Android's current reply contract
- Status: first-round same-workspace targeting landed in code; cross-workspace routing and non-reply reuse remain deferred

## Why P8 Exists

Current behavior is stable but still forces the user to find a session's latest mail before they can act on that session.

What has already landed:

- reply continuation works when the reply already points at the correct thread/session
- `/sessions` lists sessions in the current workspace
- parser already captures one optional slash-command argument as `target_session_id`

What is still missing:

- `/sessions` is read-only
- command-side retargeting is not implemented
- non-reply new mail still always creates a fresh session

This leaves a usability gap: the scheduler has session concepts, but the mail control plane still behaves as if everything were thread-local.

## Product Goal

P8 should make session targeting explicit and usable while preserving these existing rules:

- replying to a session's latest status mail must keep working exactly as it does today
- Android does not need a new mail-sending protocol for the first round
- non-reply new task mail still creates a fresh session
- the system must not guess a target session from title similarity

In short: P8 should add an explicit routing path, not replace the current reply path.

## First-Round Scope

P8 should be split into two implementation slices.

### P8A: Explicit Targeted Control In The Current Workspace

Status: landed in code.

Add explicit command-side targeting for an already known workspace.

First-round targeted commands:

- `/status <session_id>`
- `/pause <session_id>`
- `/resume <session_id>`
- `/end <session_id>`
- `/kill <session_id>`

These commands should work when the user replies inside any existing thread in the same workspace.

### P8B: Explicit Targeted Continuation

Status: landed in code for same-workspace command routing.

Add an explicit continuation command for a different session without requiring the user to first locate that session's latest mail.

Recommended first-round command:

- `/continue <session_id>`

The body below the command keeps the same update semantics as today's normal reply path:

- `Task:`
- `Acceptance:`
- `Timeout:`
- `Mode:`
- `Profile:`
- `Permission:`
- free-form continuation text

## Explicit Non-Goals

The first round of P8 should not do these things:

- no hidden auto-routing of non-reply mail by `workspace + title`
- no cross-workspace session switching
- no Android-side protocol rewrite
- no new desktop UI beyond mail control and existing observe tooling
- no attempt to merge multiple session threads into one mail thread

The `workspace + title` auto-reuse question should stay deferred until explicit targeting has proven stable.

## Routing Semantics

P8 should use these routing rules.

### 1. Workspace Boundary

The invoking mail still establishes the current workspace.

The target session must belong to the same `repo_path + workdir` workspace as the thread that received the command.

If the target session is not in that workspace, reject the command with a status mail and do not guess across workspaces.

### 2. Target Identity

The canonical selector should be `session_id`.

Optional compatibility follow-up:

- allow `thread_id` as an alias only if it resolves uniquely inside the same workspace

But the first plan should treat `session_id` as the primary user-visible identifier.

### 3. Mail-Chain Ownership

If a targeted command applies to another session, the resulting status/update mail should continue on the target session's mail chain, not on the invoking thread's chain.

This keeps native context and user-visible thread history aligned.

That means:

- `/status <session_id>` sends the status reply on the target session chain
- `/continue <session_id>` sends `[ACCEPTED]`, `[RUNNING]`, `[DONE]`, `[FAILED]`, `[QUESTION]`, or `[PAUSED]` on the target session chain

The invoking thread is only a control entrypoint, not the new owner of the target session's history.

## `/sessions` UX Direction

`/sessions` should remain the discovery entrypoint, but become actionable.

Recommended first-round output per session:

- `session_id`
- `lifecycle`
- `status`
- `session_name`
- brief summary
- copyable example commands

Example direction:

```text
Sessions in this workspace:
- thread_012 | active | done | add report export | last: Added CSV export
  /status thread_012
  /continue thread_012
  /end thread_012
```

The mail should explicitly say that targeted commands continue on the target session's own mail chain.

## `/status` Reply Content Direction

Non-running `/status` behavior can stay as-is for this plan slice. The desired running-session reply shape is narrower:

- first, explicitly say the target session is currently running,
- second, show the latest assistant-visible output for that session,
- if no assistant output is available yet, explicitly say that the session is running and that no assistant output is available yet,
- do not let reasoning-only summaries, tool-only lines, or other operator-facing diagnostics replace the assistant-visible output in this user-facing `/status` reply.

Recommended shape for a running target session:

```text
Summary: Running.
Reply:
<latest assistant output>
```

Fallback when the target session has no assistant output yet:

```text
Summary: Running.
Reply: No assistant output yet.
```

Relay migration boundary:

- this `/status` reply shape is suitable for the future VPS relay path because it stays mail-safe, short, and transport-neutral,
- it answers a user-facing progress query, not a host-liveness query,
- if Android later needs status visibility while the PC host is offline, stalled, or has not yet processed the incoming `/status` mail, that must be handled by a separate VPS-side last-known-health or heartbeat view rather than by overloading the running-session reply body.

## Implementation Plan

### Step 1: Freeze Protocol And Shared Resolution Rules

Code areas:

- `docs/current/mail_protocol.md`
- `docs/current/session_scheduler_status.md`
- `docs/plans/coding_backlog.md`
- `mail_runner/thread_store.py`
- `mail_runner/app.py`

Work:

- document that P8 adds explicit same-workspace targeting only
- add one shared helper that resolves `target_session_id` against the current workspace
- return structured failure reasons: missing target, unknown target, cross-workspace target, ended/running state mismatch when relevant

### Step 2: Land Targeted Read/Control Commands

Code areas:

- `mail_runner/app.py`
- `mail_runner/thread_store.py`
- `tests/test_app_phase*.py`
- `tests/test_intent_parser.py`

Work:

- make `/status <session_id>` load the target thread/session state
- make `/pause <session_id>`, `/resume <session_id>`, `/end <session_id>`, and `/kill <session_id>` apply to the target session
- preserve existing behavior when no target is provided
- reject invalid targets with a clear status reply

### Step 3: Land Targeted Continuation

Code areas:

- `mail_runner/models.py`
- `mail_runner/intent_parser.py`
- `mail_runner/app.py`
- `mail_runner/task_compiler.py`
- related app/parser tests

Work:

- add `CONTINUE_TARGET_SESSION` or a simpler `/continue <session_id>` parsing path
- compile the follow-up run against the target thread/session snapshot
- preserve the target session's native backend session id and message-chain ownership
- keep paused/question semantics explicit:
  - paused target still requires `/resume <session_id>`
  - waiting-for-answer target still follows answer/question rules

### Step 4: Make `/sessions` Actionable

Code areas:

- `mail_runner/app.py`
- `mail_runner/reporter.py` if formatting needs reuse
- tests covering session-list output

Work:

- upgrade `/sessions` output to show copyable commands
- make the current session obvious
- explain that targeted actions route onto the target session's own thread

### Step 5: Documentation And Acceptance

Code areas:

- `docs/current/*`
- `README.md`
- `state.md`

Work:

- document explicit targeting semantics
- keep Android compatibility notes clear
- add a controlled real-mailbox smoke only after local tests are green

## Acceptance Criteria

P8 should count as done when all of the following are true:

- replying to the latest mail of a session still works unchanged
- `/sessions` shows actionable session ids and command examples
- targeted `/status`, `/pause`, `/resume`, `/end`, and `/kill` work inside the current workspace
- `/continue <session_id>` can continue another session explicitly
- targeted actions do not guess across workspaces
- non-reply new task mail still creates a fresh session
- full regression passes

## Test Gates

At minimum, add coverage for:

- valid target session in the same workspace
- unknown target session id
- target session in a different workspace
- targeted `/resume` for ended and paused sessions
- targeted `/end` rejection for running sessions
- targeted `/continue` preserving the target session's backend resume path
- `/sessions` output showing actionable commands

## Recommended Defaults

To keep P8 small and stable, the first implementation should assume:

- same-workspace targeting only
- `session_id` as the canonical selector
- no hidden title-based reuse
- no Android behavior change
- target session chain owns the resulting status mail

## Deferred Follow-Up After P8

These can be reconsidered after the explicit path lands:

- whether non-reply mail should ever reuse an existing session by `workspace + normalized title`
- whether to accept `thread_id` aliases everywhere
- whether to add a lightweight acknowledgement mail on the invoking thread in addition to the target-thread update
