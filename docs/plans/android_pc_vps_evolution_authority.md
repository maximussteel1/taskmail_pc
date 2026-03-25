# Android / PC / VPS Evolution Authority

## Status

- Date: 2026-03-25
- Scope: current macro planning authority for the `VPS-first` multi-PC control-plane direction
- Layer: repository-scoped planning authority
- Cross-repo counterpart:
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-authority-v0.1.md`

This document aligns repository-side planning with the explicit 2026-03-25 direction reset:

- `VPS-first` unified control plane becomes the intended product mainline
- multiple `PC` nodes become first-class managed executors on one platform
- `workspace` is frozen as a `pc-scoped` local execution directory
- current mail-first / direct-sidecar slices become compatibility and closeout material, not the long-term mainline
- mail is treated as a transitional compatibility layer during cutover, not as a permanent parallel control plane

This document does **not** replace current implementation-truth docs such as:

- `docs/current/android_runner_communication_contract.md`
- `docs/current/mail_protocol.md`
- `docs/current/taskmail_direct_control_file_contract.md`

Those files still describe what is implemented today.
This file defines what the planning layer should assume next.

## Purpose

This authority exists to stop the repositories from planning against two different futures at the same time.

The older planning line assumed:

- Android direct-connect narrow slices were still the active product mainline
- repository-side `TaskMail direct relay/control/file` was still the current owner line
- `VPS ingress truth v1` was only a later successor candidate after that line closed

That is no longer the active planning baseline after the user's explicit choice to make:

- `VPS-first` control plane
- multi-PC management
- `pc-scoped workspace`

the new unique mainline.

## Current Fixed Assumptions

Unless a later authority doc reopens them, the following assumptions are now fixed for repository-side planning:

1. The intended product mainline is a `VPS-first` unified control plane, not a long-term mail-first hybrid with narrow direct sidecars.
2. `VPS` is the control-plane system of record for `pc / workspace / session / run / command / event / result / artifact metadata`.
3. `PC` remains task-execution truth; this direction change does not move repo/worktree/backend/native-session execution to the VPS.
4. One platform may manage multiple `PC` nodes concurrently.
5. `workspace` is a `pc-scoped` local execution directory, not a platform-shared resource.
6. `session` binds to one `workspace`, and therefore to one `PC`.
7. V1 does not support cross-PC hot migration of a running `session` or `run`.
8. Streaming output may become a first-class protocol object, but it must remain distinct from structured `event` and final `result`.
9. Mail may remain only as cutover-period migration/export/notification/compatibility infrastructure; the target end state after VPS cutover is to retire mail as an active control-plane and fallback lane.
10. Artifact external delivery may temporarily keep `COS` as a cutover-period compatibility lane, but the planned owner lane is the VPS-hosted file surface; after `/v1/files` cutover is operationally stable, the target end state is to retire `COS`-specific external-delivery routing rather than keep long-term dual-lane coexistence.
11. Current `docs/current/*` files remain authoritative for repository behavior until code actually changes.

## Assumptions Explicitly Retired

The following earlier assumptions are no longer active planning authority for this repository:

1. `TaskMail direct relay/control/file` is still the repository-side future mainline.
2. `Android direct-connect public plaintext` remains the macro frame that all later planning should extend.
3. `VPS ingress truth v1` should stay outside the current mainline until the existing direct line fully closes.
4. `workspace` should be treated as a platform-global shared execution object.
5. The long-term product path should keep mail-first truth and only bolt on more direct exceptions.
6. Mail should remain a permanent backup/fallback lane after the VPS-first cutover lands.

These assumptions still explain older documents and earlier validation artifacts.
They are no longer the controlling baseline for new planning work.

## Chosen Product Boundary

For the current planning line, the chosen product boundary is:

- `VPS` is the primary control plane and projection layer
- `PC` is a managed execution node
- `workspace` is owned by exactly one `PC`
- `session` and `run` are routed through `VPS`, but executed on the bound `PC`
- `backend / profile / permission / backend_transport` must become first-class execution-policy fields in the control plane rather than remaining scattered in mail-era semantics or local-only flags
- `mail`, during cutover, is derived from canonical control-plane state rather than defining it
- artifact external delivery, during cutover, may still read from temporary `COS` evidence, but the intended owner lane is `VPS /v1/files`
- after the VPS-first cutover is operationally stable, the planned direction is to retire mail as an active control-plane/fallback surface rather than keep long-term dual-path coexistence
- after the VPS-first artifact cutover is operationally stable, the planned direction is to retire `COS` as an active external-delivery lane rather than keep long-term `/v1/files + COS` coexistence

This means repository-side planning is now allowed to:

- plan around `pc registration -> workspace inventory -> command dispatch -> event/output/result`
- treat multi-PC management as a first-class platform requirement
- reuse parts of older relay/control/file work only as migration material, not as the chosen end state

This does **not** mean:

- current implementation-truth docs should be rewritten early
- business semantics may drift without an explicit protocol note
- shared-workspace multi-PC execution should be smuggled into V1

## Guardrails

The following rules remain active:

- do not misstate current repository behavior in `docs/current/*`
- do not collapse `PC execution truth` and `VPS control-plane truth` into one undocumented migration
- do not treat `workspace` as a shared mutable resource across PCs
- do not let streaming output become the only truth source
- do not casually delete current mail protocol docs while current code still behaves mail-first

## Immediate Planning Consequences

The following planning consequences now apply immediately:

1. Repository-side active mainline switches to `VPS-first multi-PC control plane`.
2. The old `TaskMail direct relay/control/file` line becomes compatibility / closeout / migration-reference material.
3. `VPS ingress truth v1` stops being a separate “afterward candidate line” and should instead be read as a useful precursor/reference inside the new mainline.
4. New active plans should organize around `command / event / output_chunk / result / artifact`, not around adding more mail/direct special cases.
5. Index docs such as `docs/plans/README.md`, `docs/plans/coding_backlog.md`, `state.md`, and repository overviews should stop describing the old direct line as the current future mainline.
6. New planning work should not assume permanent mail fallback parity; any temporary mail retention must be justified as cutover/migration scaffolding only.
7. New planning work should treat `VPS /v1/files` as the intended artifact external-delivery owner lane; any temporary `COS` retention must be justified as cutover-period compatibility scaffolding only.

## Immediate Next Steps

From this authority, the next repository-side planning steps are:

1. Freeze the repo-side reading of the new mainline in one owner note.
2. Freeze the minimal `PC <-> VPS` protocol skeleton:
   - `pc_hello`
   - `heartbeat`
   - `workspace_snapshot`
   - `execution_policy`
   - `command_dispatch`
   - `command_ack`
   - `event`
   - `output_chunk`
   - `result`
   - `artifact_manifest`
3. Stage the first repository-side implementation slices around:
   - PC node registration
   - workspace inventory
   - command routing
   - event/result persistence
4. Keep current mail/direct slices readable as compatibility baseline until actual cutover code lands.
5. Start expressing mail-related work as cutover/decommission sequencing rather than as long-term coexistence design.
6. Express `COS` retention, if any, as temporary artifact cutover/decommission sequencing rather than as a permanent second owner lane.

## Cleanup Rule

Because the older direct-relay/control/file line is no longer the active future mainline, old owner plans should remain available as reference or closeout material, but they should no longer be indexed as the repository's active mainline.
