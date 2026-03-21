# Android / PC / VPS Phase 0 Execution Plan

## Status

- Date: 2026-03-21
- Scope: execution-level plan for the first cross-repo direct-connect handshake
- Layer: Layer 2 repository execution plan
- Parent docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`
  - `docs/plans/phase0_public_plaintext_baseline.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `docs/plans/android_pc_vps_evolution_authority.md`
- Android-side counterpart:
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-plan-v0.1.md`

## Purpose

Answer one practical question:

> What must be executed first so both repositories can honestly start the public-IP plaintext direct-connect path
> without document drift?

This is not a code-only milestone.
It is the first planning-and-runtime handshake.

## Definition Of The First Handshake

For this workstream, the first handshake means:

1. both repositories explicitly accept the plaintext direct-connect direction
2. one reviewable paired baseline note freezes the same public IP, port, schemes, endpoints, token boundary, and
   fallback rule on both repositories
3. the repository has one reviewable note stating whether the live VPS already matches that baseline
4. the next implementation slice can start without any active doc still claiming that mail-first or TLS trust is the
   controlling gate

This handshake does **not** require:

- Phase 2 outbound direct action support
- inbound direct update support
- parity closeout
- mail fallback removal

## Current Repository-Side Reading

At the current repository state:

- Workstream A is aligned in docs
- Workstream B baseline freeze is aligned in docs
- Workstream C live plaintext readiness is now judged: the current live VPS now matches the plaintext baseline
- Workstream D first-slice handoff is now aligned in docs

## Execution Principles

1. Fix the planning layer before making implementation claims.
2. Keep current implementation-truth docs unchanged until code changes land.
3. Use reviewable artifact paths rather than chat memory.
4. Treat plaintext as an explicit choice, not as an accidental absence of TLS.
5. Keep mail fallback visible in the baseline package from the start.

## Workstreams

Phase 0 is split into four workstreams.

### Workstream A: Authority And Doc Realignment

Goal:

- make repository-side planning docs reflect the new direct-connect authority

Primary owner:

- PC/VPS side

Primary outputs:

- rewritten authority note
- rewritten coordinated execution plan
- updated plan index
- obsolete conflicting notes removed from the active docs set

### Workstream B: Public Plaintext Baseline Freeze

Goal:

- keep one precise baseline published and mirrored across both repositories

Primary owner:

- shared, with PC/VPS-side publishing responsibility

Required baseline items:

- accepted public host or public IP
- configured port
- plaintext `http/ws`
- `/healthz`
- `/relay`
- token boundary
- mail fallback rule

Primary outputs:

- `phase0-public-plaintext-baseline-v1`
- one short cross-repo pointer note to the same baseline package
- repository-side mirror note at `docs/plans/phase0_public_plaintext_baseline.md`

### Workstream C: Live Public Endpoint Readiness Note

Goal:

- record whether the live VPS already matches the chosen plaintext baseline

Primary owner:

- PC/VPS side

Primary outputs:

- updated relay readiness note
- explicit statement of whether the current live endpoint already matches the frozen plaintext baseline

### Workstream D: Immediate Next-Slice Freeze

Goal:

- make the first implementation slice obvious and reversible

Primary owner:

- shared

Primary outputs:

- one short note that names the Phase 1 target
- one explicit statement that mail fallback remains required
- one explicit statement of the recommended first direct action slice after bootstrap promotion
- repository-side handoff note at `docs/plans/phase0_direct_connect_handoff.md`

## Recommended Execution Order

### Step 1: Rewrite Authority

Execute first:

- align the repository-side authority with the new direction
- remove conflicting older notes from the active docs set

Reason:

- any runtime or baseline note is ambiguous if the authority layer still points somewhere else

### Step 2: Confirm The Frozen Baseline List

Execute next:

- write the exact public host or IP
- write the exact port
- write plaintext `http/ws`
- write `/healthz` and `/relay`
- write the token boundary
- write the mail fallback rule
- mirror the already-frozen Android-side baseline exactly on the repository side

Exit from this step only when:

- there is one stable cross-repo answer to "what direct endpoint are we actually building around?"

### Step 3: Judge Live VPS Readiness Against That Baseline

Execute next:

- record what the current live VPS does today
- distinguish "chosen baseline" from "current deployed reality"
- state explicitly if redeploy or reconfiguration is still needed

Exit from this step only when:

- the repository can say whether the current server matches the chosen plaintext direction or still reflects the older
  TLS-backed mode

Current verified reading from the 2026-03-21 probe:

- the service is running on the inspected VPS under systemd
- the deployed environment no longer includes `MAIL_RELAY_TLS_CERTFILE` and `MAIL_RELAY_TLS_KEYFILE`
- public `http://124.223.41.153:8787/healthz` returns `200`
- public `ws://124.223.41.153:8787/relay` completes `hello -> hello_ack`
- invalid token on public `ws://124.223.41.153:8787/relay` returns `unauthorized`
- public `https://124.223.41.153:8787/healthz` and `wss://124.223.41.153:8787/relay` now fail because the runtime is
  plaintext rather than TLS-backed

### Step 4: Freeze The Immediate Next Slice

Execute next:

- point both repositories at the same Phase 1 bootstrap target
- keep mail fallback explicit
- name the first preferred direct outbound slice after bootstrap promotion

Exit from this step only when:

- the next implementation session can begin without reopening the direction argument

## Critical Dependencies

The real dependencies for Phase 0 are:

1. authority must be aligned before execution notes can be trusted
2. the baseline list must be frozen before live readiness can be judged honestly
3. live readiness must be recorded before the first bootstrap implementation slice is treated as grounded
4. fallback must be explicit before the direct path starts growing

## Blocking Conditions

Phase 0 is blocked if any of the following remain unresolved:

- active repository planning docs still claim mail-first or TLS trust is the controlling gate
- the chosen scheme is still ambiguous between plaintext and TLS
- the token boundary is still implicit
- mail fallback is still treated as assumed rather than explicitly recorded
- the relay readiness note still judges the live path against the old TLS-gated goal instead of the chosen plaintext
  baseline

## What Can Start Immediately

The following items can start immediately on the repository side:

- authority and index realignment
- readiness-note rewrite
- first-slice freeze for bootstrap promotion
- Phase 1 bootstrap promotion against the now-live plaintext baseline

The following items are intentionally later phases:

- direct outbound business actions
- direct inbound update handling
- parity closeout
- primary-path switch

## Suggested Evidence Locations

Suggested package names:

- `phase0-public-plaintext-baseline-v1`
- `phase0-relay-readiness-v1`
- `phase0-direct-connect-handoff-v1`
- `phase1-direct-connect-bootstrap-v1`

This plan does not require those exact filenames, but it does require one explicit reviewable package per output.

## Exit Condition

This execution plan has succeeded when the repositories can start Phase 1 bootstrap work without ambiguity about:

- which public endpoint shape is accepted
- whether plaintext is intentional
- how mail fallback is supposed to behave
- and that no older conflicting note remains in the active docs set

Current repository-side reading:

- this execution plan is now satisfied at the planning-and-runtime-handshake level
- the next active work is Phase 1 bootstrap promotion
