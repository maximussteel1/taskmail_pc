# Android / PC / VPS Evolution Authority

## Status

- Date: 2026-03-21
- Scope: current macro planning authority for cross-repo Android / PC / VPS evolution after the public-IP plaintext
  decision
- Layer: repository-scoped planning authority
- Cross-repo counterpart:
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-authority-v0.1.md`

This document aligns repository-side planning with the explicit 2026-03-21 direction reset:

- Android direct-connect becomes the intended primary TaskMail path
- the near-term direct path is allowed to use the public relay entry over plaintext `http/ws`
- the current mail path remains required fallback and compatibility infrastructure

This document does **not** replace current implementation-truth docs such as:

- `docs/current/android_runner_communication_contract.md`
- `docs/current/mail_protocol.md`
- `docs/current/android_reply_method_rules.md`

Those files still describe what is implemented today.
This file defines what the planning layer should assume next.

## Purpose

This authority exists to stop the repositories from planning against two different futures at the same time.

The older planning line assumed:

- Android remains mail-first for the current phase
- `PC -> VPS` is the only stable live connection that matters
- public DNS and stock-client-trusted TLS must close before Android direct-connect work starts

That is no longer the active planning baseline after the user's explicit choice to use a public IP without certificates
for the direct path.

## Current Fixed Assumptions

Unless a later authority doc reopens them, the following assumptions are now fixed for repository-side planning:

1. The intended primary Android TaskMail path is `Android -> VPS`.
2. The accepted near-term transport baseline is one public host or public IP plus one configured port using plaintext
   `http://.../healthz` and `ws://.../relay`.
3. Token-based transport authentication is acceptable for the first direct-connect phases.
4. The current mail path must remain available as fallback and compatibility until direct parity, fallback triggers, and
   rollback behavior are explicit.
5. PC remains task-execution truth; this direction change does not move execution to the VPS.
6. Current `docs/current/*` files remain authoritative for repository behavior until code actually changes.
7. Direct-connect planning may reuse the current relay bootstrap surface pragmatically instead of waiting for a separate
   clean-room API-first redesign.

## Assumptions Explicitly Retired

The following earlier assumptions are no longer active planning authority for this repository:

1. `Android production behavior must remain mail-first for now.`
2. `PC -> VPS is the only stable live connection target that matters.`
3. `Raw /relay is not allowed to become the current Android-facing direct boundary.`
4. `Public DNS plus stock-client-trusted TLS are prerequisites before Android direct-connect work may begin.`
5. `Android must not hold relay transport credentials in the direct path.`

These assumptions still explain older documents and earlier validation artifacts.
They are no longer the controlling baseline for new planning work.

## Chosen Product Boundary

For the current planning line, the chosen product boundary is:

- Android may connect directly to the public VPS endpoint
- the near-term direct path may use plaintext transport intentionally
- `/relay` may grow from bootstrap-only use into the first real direct business-traffic lane
- `/healthz` remains diagnostic rather than business-semantic
- mail fallback remains required until direct behavior is proven strong enough to stand beside it

This means repository-side planning is now allowed to:

- treat the relay connection as more than a debug-only Android seam
- plan direct outbound and direct inbound Android slices as real product work
- coordinate around public plaintext runtime verification instead of around TLS closeout

This does **not** mean:

- current implementation-truth docs should be rewritten early
- business semantics may drift without an explicit contract note
- mail fallback may be removed casually

## Guardrails

The following rules remain active:

- do not misstate current repository behavior in `docs/current/*`
- do not commit tokens, secrets, or operator credentials into the repository
- do not remove mail fallback before parity evidence, fallback rules, and rollback behavior are explicit
- do not hide the plaintext decision behind leftover TLS-oriented wording
- do not let direct-connect planning casually rewrite TaskMail business semantics without a corresponding freeze note
- do not combine "direct-connect adoption" and "move execution truth off the PC" into one undocumented migration

## Immediate Planning Consequences

The following planning consequences now apply immediately:

1. The coordinated execution plan must be rewritten around direct-connect as the intended main path plus mail fallback.
2. Phase 0 no longer waits for DNS or certificate closeout.
3. The first closeout package must freeze:
   - accepted public host or public IP
   - configured port
   - plaintext `http/ws`
   - `/healthz`
   - `/relay`
   - token boundary
   - mail fallback rule
4. Earlier conflicting mail-first or TLS-gated repository notes should be removed from the active plan set rather than
   kept as sidecar context.
5. The current repository-side relay readiness note must now judge whether the live VPS matches the chosen plaintext
   baseline, not whether Android trusts a certificate chain.

## Immediate Next Steps

From this authority, the next repository-side planning and execution steps are:

1. Freeze the exact public plaintext baseline in one reviewable note.
2. Use the verified readiness note as the current proof that the live VPS exposes that baseline.
3. Promote the relay bootstrap seam into a reusable connection seam above debug-only use.
4. Freeze the first narrow direct outbound action contract with mail fallback preserved.
5. Keep current contract docs unchanged until code changes land.

## Cleanup Rule

Because the older mail-first and TLS-gated line is no longer the active direction, obsolete planning snapshots should be
removed from the active docs set instead of being kept around as parallel pseudo-authorities.
