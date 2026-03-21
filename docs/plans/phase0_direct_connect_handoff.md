# Phase 0 Direct-Connect Handoff

## Status

- Date: 2026-03-21
- Scope: short repository-side handoff from Phase 0 closeout into Phase 1 bootstrap promotion
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/phase0_public_plaintext_baseline.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`

## Purpose

Close the remaining Phase 0 planning question after live plaintext verification and make the next implementation slice
explicit.

## Starting Point

The current repository-side starting point is now explicit:

- public diagnostic path: `http://124.223.41.153:8787/healthz`
- public relay path: `ws://124.223.41.153:8787/relay`
- plaintext `hello -> hello_ack` is verified on the live VPS
- invalid token rejection is explicit on the live plaintext path
- mail remains the implemented user-facing baseline today

## Handoff Decisions

1. Phase 1 target is bootstrap promotion and reusable connection-seam work, not business-action cutover.
2. Mail fallback remains required for bootstrap failure, auth rejection, disconnected state, and unsupported flows.
3. The preferred first direct outbound business slice after Phase 1 is `new task`.

## Phase 1 Boundary

Phase 1 should focus on:

- moving connection assumptions above the debug-only bootstrap surface
- keeping host, port, token, and plaintext mode reachable from TaskMail flows
- keeping connection state and failure classification visible enough for later fallback routing
- avoiding expansion into direct business-action traffic before the seam is reusable

Phase 1 should not:

- treat `/healthz` as a business API
- claim mail fallback is optional
- widen the direct path into general action routing before the bootstrap seam is stable

## Phase 2 Starting Hint

Once Phase 1 closes, the preferred first direct business-action slice is:

- `new task`

All other user-facing business flows should remain on mail until Phase 2 names them explicitly.

## Current Conclusion

The repository-side Phase 0 handoff is now explicit.

Phase 0 is now closed on the repository side.

The next active implementation phase is Phase 1 bootstrap promotion on top of the live public plaintext baseline.
