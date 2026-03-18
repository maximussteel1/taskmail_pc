# PC Background Hardening Plan

## Status

- Date: 2026-03-16
- Scope: near-term hardening priorities for `mail_based_task_manager` as a long-running PC-side background process
- Source of truth for current behavior: `docs/current/*`, `README.md`, `state.md`

## Goal

This plan captures the next hardening work for the current repository as a practical background process.

It is intentionally narrower than a full platform roadmap.

It focuses on:

- keeping the runner alive and operable
- making failures easier to understand and clean up
- improving long-running maintenance characteristics

It does not try to redefine the mail protocol or expand the repository into a general task platform.

## Scope Boundary

Included in this plan:

- service hosting
- minimal observability / operator entrypoints
- interruption aftermath handling
- retention / cleanup mechanics
- second-pass mailbox ingestion hardening

Explicitly out of scope for this plan:

- sender allowlists and other security-boundary work
- major protocol redesign
- Android-side feature work
- full monitoring platform / dashboards / metrics pipeline

## Confirmed Priority Order

The current agreed order is:

1. service hosting
2. minimal observability
3. interruption aftermath
4. retention / reclamation
5. mailbox reliability second pass

This order is based on current cost / value tradeoffs, not on architectural purity.

## Priority Matrix

| Priority | Workstream | Difficulty | Expected Value | Why It Sits Here |
| --- | --- | --- | --- | --- |
| P0 | service hosting | medium | very high | the process must stay alive before any deeper runtime hardening matters |
| P1 | minimal observability | low to medium | high | cheap way to reduce operator guesswork and speed up every later phase |
| P1 | interruption aftermath | medium | high | lower-risk alternative to full crash recovery; improves failure handling without promising unsafe resume |
| P1 | retention / reclamation | low to medium | medium to high | keeps long-running state from growing without bound and is operationally clear |
| P2 | mailbox reliability second pass | medium | medium to high | important, but less urgent after the current UID + local dedupe ingestion landing |

## Workstreams

### 1. Service Hosting

**Priority**

- P0

**Difficulty**

- Medium

**Intent**

Treat the runner as a managed background service instead of a manually relaunched development loop.

**Why first**

- If the loop is not alive, the rest of the system is irrelevant.
- Hosting improvements are relatively self-contained compared with scheduler or protocol work.

**Minimum deliverable**

- one supported launch model for Windows
- single-instance protection
- clearer start / stop / restart semantics
- automatic restart behavior after abnormal exit
- explicit health / liveness check path

**Do not do yet**

- multi-host deployment
- distributed workers
- service discovery

### 2. Minimal Observability

**Priority**

- P1

**Difficulty**

- Low to medium

**Intent**

Add the smallest possible operator-facing visibility layer without building a full monitoring system.

**Why second**

- It is relatively cheap.
- It reduces debugging cost for every later workstream.
- It helps distinguish mail-ingest, scheduling, and runtime failures quickly.

**Minimum deliverable**

- `status` command with a compact summary
- `show-thread <thread_id>` command or equivalent
- `list-running`
- `list-queued`
- recent failure summary from existing state and logs

**Preferred implementation style**

- read from current `tasks/`, `_scheduler/`, and log artifacts
- avoid adding a separate database just for observability

**Do not do yet**

- dashboards
- time-series metrics
- remote telemetry
- full tracing system

### 3. Interruption Aftermath

**Priority**

- P1

**Difficulty**

- Medium

**Intent**

Handle runner restarts and interrupted runs more cleanly without promising full automatic execution recovery.

**Why third**

- It matters, but true interruption recovery is one of the highest-risk runtime features.
- A lower-risk “aftermath” layer is much more cost-effective right now than full resume semantics.

**Minimum deliverable**

- on restart, detect runs that were interrupted mid-flight
- mark them consistently with a precise reason
- preserve enough run context for diagnosis
- send or expose a clearer operator/user-facing status explanation
- make the next manual recovery path obvious (`reply`, `/rerun`, or fresh continuation)

**Important boundary**

- this is not “resume the exact interrupted process”
- this is “cleanly classify, surface, and hand off interrupted work”

**Do not do yet**

- transparent automatic backend resume after crash
- speculative replay of in-flight side effects
- guarantees that a killed process can always continue safely

### 4. Retention / Reclamation

**Priority**

- P1

**Difficulty**

- Low to medium

**Intent**

Prevent long-running local state from expanding without a lifecycle policy.

**Why fourth**

- The implementation boundary is clear.
- The value grows with runtime age rather than immediately at launch.
- It is safer to do after minimal visibility exists.

**Minimum deliverable**

- retention policy for completed / failed / killed thread state
- cleanup policy for old run directories and temporary artifacts
- log rotation or bounded log growth
- pruning rules for mailbox-ingestion local state where appropriate

**Do not do yet**

- aggressive deletion of recent troubleshooting evidence
- archival tiering beyond local filesystem needs

### 5. Mailbox Reliability Second Pass

**Priority**

- P2

**Difficulty**

- Medium

**Intent**

Harden the new mailbox-ingestion path beyond the current UID increment plus local `UID/Message-ID` dedupe baseline.

**Why fifth**

- The first critical cut already landed:
  - dual-mailbox deployment guidance
  - IMAP UID incremental scan
  - local `UID/Message-ID` dedupe
- That reduces the urgency of the next iteration.

**Minimum deliverable**

- replace JSON mailbox state with a more durable local store if needed
- add explicit protection against concurrent consumers
- improve corruption recovery for local ingestion state
- improve IMAP reconnect / retry behavior under transient failures

**Do not do yet**

- broad provider-specific behavior matrix work
- multiple independent mailbox consumers

## Recommended Execution Style

Use the following implementation rhythm:

1. land service hosting first
2. immediately add the minimal observability surface
3. use that visibility to implement interruption aftermath with less guesswork
4. then add retention rules
5. only then revisit mailbox reliability if real runtime evidence still justifies it

## Success Criteria

This plan should be considered successful when:

- the runner has one reliable supported hosting path
- an operator can answer “is it alive, what is it doing, what failed recently?” without manual filesystem archaeology
- interrupted runs produce clear aftermath states instead of ambiguous silent breakage
- local runtime artifacts no longer grow without policy
- mailbox ingestion no longer depends on ad hoc JSON-state assumptions if evidence shows that next step is needed

## Non-Goals

This plan does not claim that the repository will become:

- a full observability platform
- a self-healing distributed worker system
- a zero-downtime orchestration layer

The objective is narrower:

- a more reliable and maintainable single-machine mail-driven background process
