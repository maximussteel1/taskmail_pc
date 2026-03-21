# Phase 1 Direct-Connect Bootstrap

## Status

- Date: 2026-03-21
- Scope: repository-side Phase 1 bootstrap, connection-seam, and failure/fallback note
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`
  - `docs/plans/phase0_public_plaintext_baseline.md`
  - `docs/plans/phase0_relay_readiness_note.md`
  - `docs/plans/phase0_direct_connect_handoff.md`

## Purpose

Promote the relay bootstrap path from a one-off verification result into a reusable repository-side connection seam with
explicit failure classification.

## Repository-Side Bootstrap Seam

The repository-side bootstrap seam is now centered on:

- `mail_runner/outbound/relay_bootstrap.py`
- existing config inputs:
  - `relay_url`
  - `relay_transport_token`
  - `relay_client_id`
  - `relay_client_version`
  - `relay_timeout_seconds`
  - `relay_verify_tls`
  - `relay_ca_file`
- shared `hello` payload construction reused by `mail_runner/outbound/relay_transport.py`

This keeps bootstrap behavior in a reusable transport-facing module rather than in one debug-only probing path.

## Bootstrap Contract

The repository-side bootstrap contract is now:

1. derive `healthz` from the configured relay URL
2. probe `healthz`
3. connect to `/relay`
4. send `hello`
5. classify the result into one reviewable status rather than relying on raw exception text alone

The current probe result shape is `RelayBootstrapProbeResult`.

## Current Status Vocabulary

The repository-side bootstrap vocabulary now includes:

- success:
  - `hello_ack`
- configuration:
  - `not_configured`
- auth or protocol rejection:
  - `unauthorized`
  - `token_id_mismatch`
  - `unexpected_response`
  - `invalid_json`
- transport or scheme failure:
  - `scheme_mismatch`
  - `tls_failure`
  - `connect_failure`
  - `timeout`
  - `invalid_http_response`
  - `invalid_handshake`

This is enough for repository-side code and notes to distinguish auth/config problems from plain transport unavailability.

## Failure And Fallback Rule

For the repository side, the current Phase 1 rule is:

- `hello_ack` means the direct bootstrap path is available for later reusable-connection work
- auth failure such as `unauthorized` must be treated as a direct-path configuration or credential problem, not as a healthy direct path
- transport failures such as `scheme_mismatch`, `tls_failure`, `connect_failure`, or `timeout` must be treated as direct-path unavailability
- when the direct path is unavailable or rejected, user-facing behavior must remain on mail fallback rather than pretending direct connect is ready

## Stale And Disconnected Reading

For repository-side planning, stale or disconnected state should now be read as:

- `connected`: the most recent bootstrap classification is `hello_ack`
- `disconnected`: the most recent bootstrap classification is any non-success transport failure
- `misconfigured`: the most recent bootstrap classification is auth or configuration failure

This note does not yet add a long-lived runtime state machine.
It does make the expected categories explicit enough for later fallback routing.

## Business-Scope Boundary After Phase 1

Even after this repository-side Phase 1 artifact, user-facing business actions remain mail-first until Phase 2 defines
the first direct action slice explicitly.

The preferred first direct business slice remains:

- `new task`

Everything else remains mail-only until a later Phase 2 scope note widens it explicitly.

## Current Conclusion

Repository-side Phase 1 bootstrap artifacts are now strong enough to support:

- a reusable bootstrap probe
- a reusable connection-seam boundary
- explicit failure and fallback classification

Cross-repo Phase 1 is still not declared closed here.
Android-side reuse above the debug-only surface remains a separate closeout dependency.
