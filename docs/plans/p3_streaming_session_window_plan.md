# P3 Streaming Session Window Plan

> Document layer: Layer 2 (repository implementation plan)
>
> Scope: `mail_based_task_manager` current repository only
>
> This document refines P3 from `docs/plans/coding_backlog.md` into an implementation-ready plan.
> It does not change current runtime behavior by itself.

## Status

- Date: 2026-03-18
- Backlog slot: P3 observability polish
- Depends on: `codex_sdk_continuous_session_plan.md`
- Source of truth for current behavior: `docs/current/*`

## Goal

Provide a local PC-side session window for active Codex SDK runs that can show:

- the thread conversation history
- live assistant output as it is produced
- timestamps for user-visible updates
- a small runtime status footer

The first cut is intentionally narrow. It is a repository-owned local observability surface, not an attempt to embed the official Codex TUI.

## Why This Exists

The current repository already has:

- durable thread/mail history under `tasks/<thread>/mail/raw_*.json`
- transcript reconstruction after the fact
- a minimal `observe` CLI
- Windows monitor windows that can auto-open per running thread

What it does not have is a durable mid-turn event stream.

Today the SDK path is still effectively one-shot:

- Python starts a short-lived Node sidecar
- the sidecar runs one turn
- Python waits for the final payload
- only the final response is persisted in a user-visible way

That is enough for completion, but not enough for a live session window.

## First-Cut Scope

Included:

- `backend=codex` with `backend_transport=sdk` only
- live assistant text streaming
- timestamps on streamed updates
- selected high-signal tool/runtime events
- thread-focused Windows monitor window rendering
- durable per-run event log stored under the run directory
- read-only local observation

Excluded:

- `opencode` parity
- CLI transport parity
- remote dashboards or telemetry
- mail protocol changes
- interactive user attach or prompt injection during a running turn
- guarantees that cancel equals reliable process-tree kill

## Product Definition

The target window is a thread-scoped session view:

1. Historical transcript is rendered from existing archived mail turns.
2. If the thread is currently running on the Codex SDK transport, the window appends live assistant output from the active run.
3. The bottom of the window keeps a compact runtime strip with task id, backend transport, started time, and current health/status.

This means the view is a merge of:

- stable transcript history
- transient live run events

The stable transcript remains the truth for completed turns.
The live event log exists to cover the gap while a turn is still in progress.

## Key Design Decisions

### 1. SDK-first only

The first cut must target `codex + sdk` only.

Reason:

- it is the only current path that has a plausible structured event source
- trying to unify CLI and SDK streaming in the first cut would make the design much larger
- the repository already persists `backend_transport`, so the product boundary is clear

### 2. Additive local observability layer

Streaming should be implemented as a local event layer under the run directory, not by changing mail artifacts or the thread transcript format.

Reason:

- keeps mail protocol stable
- avoids contaminating archival transcript with partial/incomplete assistant output
- allows monitor windows to be rebuilt from local files after reopen/restart

### 3. Thread view, not raw process view

The PC window should remain thread-oriented, not sidecar-oriented.

Reason:

- the user thinks in threads/sessions, not subprocess ids
- the repository already indexes state by thread and task
- the same thread view should later be able to show "no live run, only history"

### 4. Read-only first

No user intervention in the first cut beyond existing external controls such as kill/pause paths already owned by the runner.

Reason:

- streaming already requires changing the execution contract
- attach/intervention would require a second design layer for input routing, permissions, and cancel semantics

## Current Baseline

The main current constraints are:

- `CodexSdkAdapter` runs a short-lived sidecar and waits on `process.communicate()`
- the Node sidecar uses `thread.run(prompt)` rather than a streamed event loop
- `observe` only exposes summary-level status and thread metadata
- transcript export reconstructs completed turns from archived mail, not live in-flight output

This means P3 streaming is not a presentation-only task.
It requires a new durable event path from sidecar to local filesystem.

## Proposed Architecture

### Layer A: Sidecar streamed turn execution

Replace the sidecar's one-shot turn call with a streamed turn path.

Responsibilities:

- start or resume the Codex SDK thread
- consume streamed events from the SDK
- normalize them into a repository-owned event schema
- append them to `stream.events.jsonl`
- emit a final compact result payload on completion

The sidecar remains a thin adapter.
Business semantics stay in Python where possible.

### Layer B: Python adapter orchestration

`CodexSdkAdapter` remains the owner of run lifecycle and `RunResult` assembly.

Responsibilities:

- create run directory and stream log path
- pass stream-related paths/env vars to the sidecar
- keep current summary/result behavior
- preserve best-effort kill behavior
- treat streaming as additive observability, not as a new success/failure protocol

### Layer C: Observe live view

Add a new `observe` command for thread-scoped live rendering.

Recommended command:

- `show-thread-live <thread_id>`

Responsibilities:

- load stable transcript history from archived mail
- detect whether the thread has a currently running task
- if the active task is `codex + sdk`, merge `stream.events.jsonl`
- render a text UI suitable for repeated refresh in PowerShell

### Layer D: Monitor window

Retain the current Windows auto-open behavior, but change the focused view from summary-only to conversation-first.

Responsibilities:

- call `show-thread-live <thread_id>`
- refresh at the configured interval
- keep a stable title per thread
- auto-close when the thread is no longer running

## Event Log Design

### File location

Per-run file under the run directory:

- `runs/<task_id>/stream.events.jsonl`

Optional companion metadata file if later needed:

- `runs/<task_id>/stream.meta.json`

The first cut should avoid introducing more files unless necessary.

### Event goals

The event schema should be:

- append-only
- reconstructable after window reopen
- stable enough for tests
- independent from raw SDK event names

### Recommended normalized event types

- `turn.started`
- `assistant.delta`
- `assistant.completed`
- `tool.started`
- `tool.completed`
- `status`
- `turn.completed`
- `turn.failed`
- `turn.cancel_requested`

The first cut should not attempt to preserve every low-level SDK item.
It should keep only the events that improve human visibility.

### Recommended event fields

Each line should be a JSON object with:

- `ts`: ISO timestamp generated when the event is written
- `seq`: monotonically increasing integer per run
- `thread_id`
- `task_id`
- `backend`: `codex`
- `backend_transport`: `sdk`
- `kind`: normalized event type
- `text`: optional full text or delta
- `delta`: optional incremental assistant text
- `item_type`: optional normalized SDK item type
- `status`: optional run/thread status snapshot
- `payload`: optional small structured metadata object

### Assistant text rule

The window should be driven by `assistant.delta` events and reconstruct the current message by append order.

Reason:

- simpler than repeated full snapshots
- efficient for file append
- recoverable on window reopen by replaying the log

`assistant.completed` should mark the end of the current assistant message block.

## Rendering Model

The live thread renderer should output three sections:

1. Thread header
2. Transcript body
3. Runtime footer

### Thread header

Include:

- thread id
- current task id
- backend transport
- started time
- current lifecycle/run status

### Transcript body

Render completed transcript turns first, then the live in-progress assistant block if present.

Recommended rules:

- preserve timestamps for each completed turn from the archived mail transcript
- render live assistant updates with the local event timestamp
- visually distinguish completed turns from in-progress output
- collapse noisy tool details to short status lines

### Runtime footer

Keep a compact footer with:

- running or idle
- last stream event time
- active transport
- current task id
- hint when the thread is no longer running

## Persistence And Recovery

The plan should support the following cases:

- monitor window closed and reopened during a live run
- host process survives while the window exits
- live run completes before the next refresh

Required behavior:

- replaying `stream.events.jsonl` must rebuild the visible in-progress assistant block
- once the run completes, the final mail/status pipeline remains unchanged
- the stream log is an observability artifact, not the archival source of truth

If the stream log is missing, unreadable, or partial, `show-thread-live` should degrade to:

- archived transcript only
- plus a short note that live stream data is unavailable

## Command Surface

Recommended new `mail_runner.observe` command:

- `show-thread-live <thread_id>`

Optional later follow-ons, not required in the first cut:

- `tail-run-stream <thread_id>`
- `export-stream-log <thread_id>`

The first cut should keep the command surface minimal.

## Phased Implementation

### Phase P3.1: event spine

Deliverables:

- sidecar streamed event consumption
- per-run `stream.events.jsonl`
- adapter wiring and final result compatibility

Done when:

- a live SDK run writes append-only events during the turn
- final result behavior remains compatible with existing summaries and run results

### Phase P3.2: live observe command

Deliverables:

- `show-thread-live <thread_id>`
- merge logic for archived transcript plus live run events
- graceful fallback when there is no stream

Done when:

- the command can render a meaningful live thread view in the terminal

### Phase P3.3: monitor window integration

Deliverables:

- monitor script uses the new live thread command
- per-thread windows stay focused on conversation-first rendering
- auto-close behavior remains intact

Done when:

- active SDK threads open a PC window that shows live conversation output with timestamps

### Phase P3.4: polish

Deliverables:

- concise tool event summaries
- footer improvements
- truncation and wrapping behavior
- test coverage for replay and degradation cases

Done when:

- the window is usable for day-to-day operator visibility without overwhelming noise

## Testing Strategy

At minimum add targeted tests for:

- sidecar event normalization
- adapter behavior when stream file exists, is empty, or is partial
- observe rendering with:
  - archived transcript only
  - archived transcript plus live assistant delta
  - corrupted or missing stream log
- monitor script behavior when the thread stops running

Prefer repository-local deterministic fixtures over integration tests that depend on a real interactive SDK stream.

## Risk Assessment

### Medium risk

- mapping SDK streamed items into a stable repository schema
- keeping the live window readable instead of noisy
- preserving backward compatibility with current result handling

### High risk

- assuming cancel semantics are stronger than they really are
- trying to support all transports in the first cut
- leaking backend prompt boilerplate into the user-facing conversation view

## Explicit Non-Goals

This plan does not attempt to deliver:

- a general desktop UI
- a remote monitoring service
- full event tracing
- interactive attach or prompt injection into a running turn
- cross-backend streaming parity

Those can be revisited later, but they should not be coupled to the first streaming observability cut.

## Recommended Start Order

1. Upgrade the sidecar to emit normalized stream events.
2. Teach `CodexSdkAdapter` to own the stream artifact path.
3. Add `show-thread-live <thread_id>` to `observe`.
4. Switch the Windows monitor window to the new live thread renderer.
5. Only after that, discuss whether controlled local intervention is worth a second design pass.

## Done When

This plan is complete when the repository can do all of the following without changing the mail protocol:

- run a Codex SDK turn
- persist live per-run stream events locally
- reconstruct a thread-scoped live session window from transcript plus stream log
- show timestamps for historical turns and in-progress assistant output
- degrade cleanly when live stream data is unavailable
