# Android / PC / VPS Coordinated Execution Plan

> 2026-03-25 note:
>
> This document is no longer the active repository-side mainline owner plan.
>
> It is now a legacy direct-connect staged plan kept for closeout and migration reference only.
>
> Current future-direction authority:
>
> - `docs/plans/android_pc_vps_evolution_authority.md`
> - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`

## Status

- Date: 2026-03-21
- Scope: legacy staged execution plan for the public-IP plaintext direct-connect direction
- Layer: Layer 2 repository plan
- Assumption: owner-operated system with one primary user path
- Related docs:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/android_pc_vps_phase0_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-plan-v0.1.md`

## Purpose

Turn the new direction choice into one staged plan both repositories can follow:

- direct-connect is the intended Android main path
- current mail behavior remains the implemented baseline today
- mail stays available as fallback while the direct path is built

This document is no longer planning authority for the active mainline.
It remains useful only as staged historical context for the older direct-connect line.

## Current Planning Position

The current repository-side planning position is:

- Android direct-connect is the intended main path
- the chosen near-term runtime baseline is public host or public IP plus plaintext `http/ws`
- `/healthz` remains the diagnostic endpoint
- `/relay` is the connection and first direct-traffic endpoint
- token auth is acceptable for the first phases
- mail fallback remains required during rollout
- current code and current docs are still largely mail-first today

The plan is intentionally pragmatic.
It prefers reusing the existing relay bootstrap seams over waiting for a later clean-room app-facing API redesign.

## Fixed Planning Inputs

The following assumptions are treated as fixed unless a later authority doc changes them:

1. Current implementation-truth remains in `docs/current/*`.
2. The first direct path may use plaintext transport intentionally.
3. PC remains task-execution truth.
4. Mail fallback must remain available until parity and rollback are explicit.
5. The first direct slices should be narrow and reversible.
6. Earlier conflicting mail-first or TLS-gated notes should not remain in the active plan set.

## Shared Artifact Rule

Every phase should leave behind one explicit package or note that both repositories can reference by name.

Recommended package types:

- direct-connect baseline note
- bootstrap verification package
- direct outbound contract note
- direct inbound mapping note
- parity checklist and mismatch ledger
- rollback and fallback note

If a phase has only chat conclusions, it is not ready to close.

## Phase Overview

The active staged path is:

1. Phase 0: direction reset and baseline freeze
2. Phase 1: bootstrap promotion and reusable connection seam
3. Phase 2: direct outbound action bridge
4. Phase 3: direct inbound update bridge
5. Phase 4: dual-stack parity and primary-path switch
6. Phase 5: long-term default hardening

## Current Phase Reading

The current repository-side reading is:

- Phase 0 planning sync is aligned
- Phase 0 and Phase 1 repository-side planning artifacts are already explicit
- the shared public plaintext baseline is frozen and live
- Phase 2 direct outbound v1 is now implemented and validated on the repository side
- the current direct outbound scope remains intentionally narrow:
  - `new_task` only
- accepted direct send, fallback-classified rejection, and hard rejection have all been closed through live validation
- the first repository-side Phase 3 mapping artifact is now explicit for active-session read-side work
- the repository-side Phase 3 wire freeze and representative fixture package are now also explicit
- the repository-side Phase 3 closeout handoff is now explicit and aligned to the paired Android-side Phase 4-start reading
- the next active cross-repo implementation slice is now Phase 4 dual-stack parity and primary-path switch
- the repository-side Phase 4 execution plan is now explicit and starts with `new_task` as the conservative covered flow

## Phase 0: Direction Reset And Baseline Freeze

### Intent

Make the planning layer internally consistent before implementation claims begin.

### PC / VPS Deliverables

- align repository-side authority docs to the plaintext direct-connect decision
- keep the frozen baseline list explicit for:
  - accepted public host or IP
  - configured port
  - plaintext `http/ws`
  - `/healthz`
  - `/relay`
  - token boundary
  - mail fallback rule
- record whether the live VPS already matches that baseline or still reflects the earlier TLS-backed deployment

### Android Deliverables

- Android-side authority and staged plan for the new direction
- cleanup of earlier mail-first or TLS-gated planning notes so they no longer compete with the active path

### Shared Freeze Artifacts

- `phase0-public-plaintext-baseline-v1`
- one short cross-repo handoff note that points both repositories to the same direction reset

### Exit Gate

Phase 0 is complete only when:

- no active planning doc still treats mail-first or TLS trust as the gate for starting direct-connect work
- the public plaintext baseline is explicit and reviewable
- no older conflicting plan note remains in the active docs set

## Phase 1: Bootstrap Promotion And Reusable Connection Seam

### Intent

Promote the current relay bootstrap seam into a reusable direct-connect foundation without removing mail fallback.

### PC / VPS Deliverables

- verify the intended public `http/ws` endpoint behavior on the chosen host or IP
- keep plaintext support explicit in relay runtime and deployment notes
- keep `healthz`, `hello`, `hello_ack`, and token admission behavior reviewable
- publish connection and failure expectations that Android can consume

### Android Deliverables

- move connection logic above the debug-only screen
- keep host, port, token, and plaintext mode reachable from internal TaskMail flows
- surface connection state and fallback-to-mail behavior

### Shared Freeze Artifacts

- `phase1-direct-connect-bootstrap-v1`
- bootstrap verification package for `http /healthz` and `ws /relay`
- failure and fallback note

### Exit Gate

Phase 1 is complete only when:

- Android can establish the public plaintext relay session reliably enough to be reused outside the debug screen
- failure states are visible
- the system can route back to mail fallback when direct connect is unavailable or rejected

## Phase 2: Direct Outbound Action Bridge

### Intent

Make the direct path capable of the first high-value outbound actions while mail remains available as fallback.

### Recommended First-Scope Actions

- new task
- plain continuation reply
- `/status`
- `/pause`
- `/resume`
- `/end`

### PC / VPS Deliverables

- define the first direct outbound contract with Android
- accept and route first-scope actions over the direct path
- preserve current mail composition and reply behavior as fallback
- record direct-send versus mail-fallback outcomes explicitly

### Android Deliverables

- serialize the first-scope actions into the agreed direct payloads
- keep mail compose as fallback for unsupported or failed actions
- expose enough debug evidence to distinguish direct success from fallback

### Shared Freeze Artifacts

- `phase2-direct-outbound-contract-v1`
- first-scope payload examples
- fallback matrix for supported versus unsupported actions

### Exit Gate

Phase 2 is complete only when:

- first-scope outbound actions can travel over the direct path
- failures can fall back cleanly to mail
- both repositories can explain the exact current supported direct action set

## Phase 3: Direct Inbound Update Bridge

### Intent

Make Android capable of consuming direct-side updates strongly enough to drive the existing TaskMail UI while mail still
coexists.

### PC / VPS Deliverables

- define how direct updates represent:
  - workspace summary changes
  - session timeline changes
  - question state
  - paused and running state
  - done and failed completion state
- keep update semantics reviewable and bounded
- avoid accidental divergence from established TaskMail business meaning

### Android Deliverables

- map direct updates into the existing repository and UI boundaries where possible
- let mail-derived and direct-derived state coexist during transition
- record unsupported or deferred inbound cases explicitly

### Shared Freeze Artifacts

- `phase3-direct-inbound-mapping-v1`
- `phase3-direct-inbound-fixture-package-v1`
- coexistence note for mail-derived and direct-derived state

### Exit Gate

Phase 3 is complete only when:

- Android can maintain useful read-side state from the direct path
- mail fallback is still available
- both repositories agree how direct updates map into existing UI concepts

## Phase 4: Dual-Stack Parity And Primary-Path Switch

### Intent

Make direct-connect the practical primary path for covered flows while preserving rollback credibility.

### PC / VPS Deliverables

- provide stable enough direct behavior for parity comparison
- keep mail fallback operational
- publish explicit mismatch-triage and rollback expectations

### Android Deliverables

- compare representative direct-path outcomes against current mail-derived outcomes
- record mismatches explicitly
- switch covered flows to direct as the primary route only once parity is good enough

### Shared Freeze Artifacts

- parity checklist
- mismatch ledger
- rollback trigger note

### Exit Gate

Phase 4 is complete only when:

- direct path is the default for the covered flows
- mail fallback remains available and tested
- rollback rules are explicit enough to be credible

## Phase 5: Long-Term Default Hardening

### Intent

Stabilize the chosen direct default rather than treating it as a temporary experiment.

### PC / VPS Deliverables

- document long-term token handling and rotation expectations
- harden reconnect, stale-session, and replay behavior
- close the biggest direct-versus-mail edge cases

### Android Deliverables

- keep explicit operator and user-facing fallback behavior
- harden reconnect and stale-session handling
- keep the rollback path alive instead of letting it decay into dead code

### Shared Freeze Artifacts

- long-term fallback note
- token and reconnect handling note
- remaining edge-case ledger

### Exit Gate

Phase 5 is complete only when:

- direct-connect is stable enough to be treated as the normal route
- fallback is still real operational behavior
- both repositories can explain the remaining direct-versus-mail edge cases honestly

## Immediate Next Slice

From the current repository baseline, the recommended next slice is:

1. publish `phase3-direct-inbound-mapping-v1` for one narrow read-side slice
2. keep mail-derived state as the current fallback and coexistence truth
3. start with active-session snapshot plus delta, not a general history API
4. map only current high-signal read-side concepts first:
   - running
   - question
   - paused
   - done
   - failed
5. leave direct reply/control and attachment/history expansion to later explicit scope notes

## Explicit Non-Goals

- pretending current repository behavior is already direct-connect-first
- removing the mail path early
- hiding the plaintext decision behind leftover TLS language
- treating `/healthz` as a business API
- bundling "direct-connect adoption" with "move execution truth to VPS"
- waiting for a separate clean-room API program before reusing the existing relay seam

## Success Condition

This plan is successful when both repositories can answer, at any point in time:

- which phase is currently active
- what the current direct-connect boundary is
- which flows are direct, which are mail, and which are fallback-only
- what evidence is still missing before the next phase may start
- and how the current codebase still differs from the chosen direction
