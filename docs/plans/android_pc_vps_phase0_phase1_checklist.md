# Android / PC / VPS Phase 0-1 Checklist

## Status

- Date: 2026-03-21
- Scope: detailed checklist for Phase 0 and Phase 1 of the public-IP plaintext direct-connect path
- Layer: Layer 2 repository checklist
- Parent plan:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
- Related docs:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/android_pc_vps_phase0_execution_plan.md`
  - `docs/plans/phase0_public_plaintext_baseline.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `docs/plans/phase1_direct_connect_bootstrap.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-plan-v0.1.md`

## Purpose

Turn the first two phases of the new direction into a concrete checklist:

- Phase 0: direction reset and baseline freeze
- Phase 1: bootstrap promotion and reusable connection seam

This checklist is intentionally near-term.

## How To Use This Checklist

Recommended use:

1. treat each work package as incomplete until its listed artifacts exist
2. keep evidence paths explicit instead of relying on chat history
3. do not mark a phase complete only because code or a decision exists
4. keep current implementation-truth separate from future-direction planning

Suggested artifact naming convention:

- `phase0-public-plaintext-baseline-v1`
- `phase0-relay-readiness-v1`
- `phase0-direct-connect-handoff-v1`
- `phase1-direct-connect-bootstrap-v1`

## Phase 0 Goal

Make the planning layer consistent around the chosen public plaintext direct-connect path.

## Phase 0 Work Package A: Authority And Planning Realignment

### Objective

Ensure no active repository-side planning doc still frames mail-first or TLS trust as the gate for starting
direct-connect work.

### Checklist

- [x] rewrite the repository-side macro authority around the plaintext direct-connect decision
- [x] rewrite the coordinated execution plan around direct-connect main path plus mail fallback
- [x] update the plan index so readers can find the new active path quickly
- [x] remove earlier mail-first or TLS-gated repository notes that no longer have execution value

### Required Artifacts

- updated authority note
- updated coordinated execution plan
- updated plan index
- active-doc cleanup of earlier conflicting notes

### Done When

- no active repository plan still describes the earlier mail-first or TLS-gated path as the controlling baseline

## Phase 0 Work Package B: Public Plaintext Baseline Freeze

### Objective

Freeze one precise baseline for the chosen direct path and mirror the same values already frozen on Android side.

### Checklist

- [x] record the accepted public host or public IP
- [x] record the configured port
- [x] record plaintext `http`
- [x] record plaintext `ws`
- [x] record `/healthz` as the diagnostic endpoint
- [x] record `/relay` as the connection endpoint
- [x] record the token storage and transport boundary
- [x] record the mail fallback rule

### Required Artifacts

- `phase0-public-plaintext-baseline-v1`
- repository-side mirror note at `docs/plans/phase0_public_plaintext_baseline.md`
- one short cross-repo pointer note to the same baseline package

### Done When

- the repositories can point to one exact cross-repo answer for "what endpoint shape are we building around?"

## Phase 0 Work Package C: Live Relay Readiness Against The Plaintext Baseline

### Objective

State whether the live VPS already matches the chosen plaintext baseline or still reflects the earlier TLS-backed mode.

### Checklist

- [x] record the current public host or IP evidence available in this repository
- [x] record whether the runtime code can intentionally run without TLS
- [x] record current live public `http /healthz` behavior
- [x] record current live public `ws /relay` behavior
- [x] distinguish "chosen baseline" from "current deployed reality"
- [x] state explicitly if redeploy or reconfiguration is still needed

### Required Artifacts

- updated relay readiness note
- one explicit current-state judgment for the live public endpoint

### Done When

- the repository can explain whether the live VPS already matches the new direction or still needs runtime changes

## Phase 0 Work Package D: Immediate Next-Slice Freeze

### Objective

Make the first implementation slice obvious, narrow, and reversible.

### Checklist

- [x] record that Phase 1 target is bootstrap promotion, not business-action cutover
- [x] record that mail fallback remains required
- [x] record the preferred first direct outbound slice after bootstrap promotion
- [x] publish one short handoff note both repositories can reuse

### Required Artifacts

- `phase0-direct-connect-handoff-v1`
- one explicit fallback note

### Done When

- the next implementation session can begin without reopening the direction debate

## Phase 0 Closeout Gate

Do not mark Phase 0 complete until all of the following are true:

- Work Package A is complete
- Work Package B is complete
- Work Package C is complete
- Work Package D is complete
- both repositories can point to the same direct-connect baseline
- the live-readiness note is written against the plaintext baseline rather than the older TLS gate

Current repository-side reading:

- Work Package A: aligned
- Work Package B: aligned
- Work Package C: verified, current live VPS now matches the plaintext baseline
- Work Package D: aligned

Phase 0 repository-side closeout reading:

- planning freeze: aligned
- live plaintext readiness: verified
- immediate handoff: aligned
- Phase 0 repository-side status: closed
- next active phase: Phase 1 bootstrap promotion

## Phase 1 Goal

Promote the existing bootstrap seam into a reusable direct-connect foundation while keeping mail fallback alive.

## Phase 1 Work Package A: Public Plaintext Bootstrap Verification

### Objective

Verify the intended public `http/ws` path strongly enough to support reusable Android connection logic.

### Checklist

- [x] verify `http /healthz` on the chosen public host or IP
- [x] verify `ws /relay`
- [x] verify plaintext `hello -> hello_ack`
- [x] verify token rejection behavior is explicit
- [x] verify connection failure states are classifiable

Current verified result on 2026-03-21:

- public `http://124.223.41.153:8787/healthz` returned `200 OK` with `tls_enabled = false`
- public `ws://124.223.41.153:8787/relay` returned `hello_ack`
- invalid token on `ws://124.223.41.153:8787/relay` returned `unauthorized`
- public `https://124.223.41.153:8787/healthz` and `wss://124.223.41.153:8787/relay` now fail because the live runtime is plaintext

### Required Artifacts

- public bootstrap verification package
- `hello -> hello_ack` record
- failure classification note

### Done When

- the repository has a reusable proof package for the direct bootstrap path

## Phase 1 Work Package B: Reusable Connection Seam

### Objective

Move connection assumptions above a debug-only entry point.

### Checklist

- [x] define the reusable connection seam boundary
- [x] keep host, port, token, and plaintext mode reachable from internal TaskMail flows
- [x] keep connection state visible enough for later fallback routing
- [x] avoid coupling the seam to one debug-only UI surface

### Required Artifacts

- seam note or implementation record
- connection-state note

### Done When

- connection logic is no longer conceptually trapped inside a debug-only bootstrap surface

## Phase 1 Work Package C: Failure And Fallback Behavior

### Objective

Make direct-connect failure behavior explicit before business-action routing begins.

### Checklist

- [x] define when direct-connect should fall back to mail
- [x] define how auth failure differs from connectivity failure
- [x] define how stale or disconnected state is surfaced
- [x] define what remains mail-only after Phase 1 closes

### Required Artifacts

- fallback note
- failure-state note

### Done When

- the repository can explain exactly how Phase 1 failure routes back to mail behavior

## Phase 1 Work Package D: Handoff To The First Direct Action Slice

### Objective

Set up a controlled transition into Phase 2 rather than expanding direct traffic ad hoc.

### Checklist

- [x] name the first preferred direct outbound slice, such as `new task` or `/status`
- [x] state that other flows remain on mail until Phase 2 defines them
- [x] keep rollback to mail explicit for the first direct slice

### Required Artifacts

- short Phase 2 handoff note
- first-slice scope note

### Done When

- the repositories can start Phase 2 without guessing which direct action comes first

## Phase 1 Closeout Gate

Do not start Phase 2 direct outbound work until all of the following are true:

- Work Package A is complete
- Work Package B is complete
- Work Package C is complete
- Work Package D is complete
- the public bootstrap path has one reusable verification package
- mail fallback behavior is explicit rather than assumed

Current repository-side Phase 1 reading:

- Work Package A: aligned
- Work Package B: aligned in code and note
- Work Package C: aligned in note
- Work Package D: aligned by the repository-side handoff note
- repository-side next dependency: Android-side reuse above the debug-only bootstrap surface
- cross-repo Phase 1 closeout: still open

## Explicitly Out Of Scope For This Checklist

- direct outbound business-action parity
- direct inbound update parity
- primary-path switch
- mail fallback removal
- execution-truth migration away from the PC
