# Phase 0 Public Plaintext Baseline

## Status

- Date: 2026-03-21
- Scope: repository-side freeze note for the shared public-IP plaintext direct-connect baseline
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/android_pc_vps_phase0_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-phase0-public-plaintext-baseline-v1.md`

## Purpose

Freeze the exact baseline that both repositories should treat as the Phase 0 public plaintext direct-connect target.

This note freezes the planning baseline only.
It does **not** claim that the current live VPS already serves this runtime shape end to end.

## Frozen Baseline

The current shared Phase 0 public plaintext baseline is:

- public IP: `124.223.41.153`
- configured port: `8787`
- diagnostic endpoint: `http://124.223.41.153:8787/healthz`
- connection endpoint: `ws://124.223.41.153:8787/relay`
- transport auth: token-based bootstrap/auth admission
- token boundary: the direct-connect transport token may be held locally for bootstrap by the participating runtime, but
  it must never be echoed in tracked docs, logs, screenshots, or mail bodies
- fallback rule: if host, port, token, connect, or `hello -> hello_ack` bootstrap fails, the user flow remains on the
  current mail path rather than pretending the direct path is available

## Cross-Repo Interpretation

This note is intended to mirror the Android-side Phase 0 baseline freeze exactly on the repository side.

That means:

- the chosen Phase 0 target is no longer just "one public host or IP"
- it is now this exact public IP, port, endpoint pair, token boundary, and fallback rule
- Phase 0 planning sync should be judged against this exact baseline rather than against generic placeholders

## Current Readiness Boundary

This baseline freeze does not close the runtime-readiness question by itself.

The paired readiness note now records that the inspected live public deployment matches this plaintext baseline.

So the current state is:

- baseline freeze: aligned
- live plaintext deployment: verified
- Phase 0 handoff: explicit
- Phase 0 repository-side status: closed
- next active slice: Phase 1 bootstrap promotion
