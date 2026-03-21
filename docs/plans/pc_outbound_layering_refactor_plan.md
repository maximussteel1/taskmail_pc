# PC Outbound Layering Refactor Plan

## Status

- Date: 2026-03-20
- Scope: repository-scoped refactor plan for the current PC-side outbound path in `mail_based_task_manager`
- Layer: Layer 2 repository plan
- Primary inputs:
  - `docs/task_manager_migration_tasklist.md`
  - `docs/current/pc_mail_output_protocol.md`
  - `docs/current/multimedia_mail_protocol.md`
  - `docs/plans/p9_html_mail_projection_plan.md`
  - `docs/plans/pc_outbound_layering_first_slice_checklist.md`
- Implementation progress:
  - The repo-side layering work for Phases 0-5 is structurally landed in code and tests.
  - The current production outbound transport remains `email`.
  - `relay_transport.py` now exists only as a stub/registration seam; real relay/VPS delivery remains deferred.
- Relation to P9:
  - P9 HTML work remains temporarily frozen.
  - This plan keeps the frozen outbound contract unchanged and only refactors internal PC-side boundaries.

## Goal

This plan is the repository-side follow-up to the migration tasklist.

The immediate goal is:

1. keep the current email workflow and frozen HTML contract stable,
2. split the current outbound path into render / packet / transport layers,
3. make future stable-connection work start by adding a new transport instead of rewriting `app.py`, `reporter.py`, or `mail_io.py`.

This plan does **not** change the Android communication protocol.

## Parallel Work Boundary

The PC project and Android project can proceed in parallel as long as the current outbound contract remains unchanged.

For this repository, that means:

- do not change the Android-facing HTML / plain-text / attachment contract,
- do not move reply truth into HTML,
- do not introduce relay/VPS protocol fields into current mail artifacts,
- treat cross-project coordination as a contract-freeze check, not as an implementation blocker.

## Hard Boundaries

- Do not change Android communication protocol in this plan.
- Do not resume or broaden P9 HTML scope here.
- Do not change subject-shape semantics, `text/plain` truth, or reply routing.
- Do not redesign scheduler, mailbox ingest, or thread lifecycle here.
- Do not implement real relay/VPS transport logic yet.
- Keep email as the only production transport during this refactor.

## Current Progress Snapshot

As of 2026-03-20, the repository has already landed the internal outbound split under `mail_runner/outbound/`:

- `service.py` owns workflow orchestration.
- `renderer.py` owns the thin subject/plain/html render facade over `reporter.py`.
- `contract.py` and `packet_builder.py` define the packet and dispatch-request handoff.
- `dispatcher.py`, `email_transport.py`, and `journal.py` define the transport seam plus local delivery-attempt recording.
- `relay_transport.py` is present as a stub only; it is intentionally not a real remote transport yet.

This means the structural refactor goal of this plan is effectively complete.
What remains deferred is not another internal layering step, but the future workstream that implements a real relay/VPS transport behind the existing seam.

## Current Code Reality

The current outbound chain is already close to a refactor seam, but the composition still lives mostly inside `mail_runner/app.py`.

Current split in practice:

- `mail_runner/app.py`
  - resolves artifacts,
  - applies external delivery rules,
  - builds Markdown / plain-text / HTML,
  - builds subject and reply headers,
  - sends mail,
  - stores outgoing mail records,
  - prunes old status mail.
- `mail_runner/reporter.py`
  - already owns most content rendering behavior.
- `mail_runner/mail_io.py`
  - already owns SMTP + MIME assembly.
- `mail_runner/external_delivery.py`
  - already owns oversized-artifact externalization.
- `mail_runner/artifact_resolver.py`
  - already owns artifact truth-layer resolution and outgoing attachment projection.

This means the lowest-risk next move is **not** a rewrite.
It is an extraction plan that wraps existing logic into clearer boundaries first, then inserts the packet stage, then inserts the transport abstraction.

## Recommended Repo Shape

Recommended new subpackage:

```text
mail_runner/
  outbound/
    __init__.py
    contract.py
    normalizer.py
    renderer.py
    packet_builder.py
    dispatcher.py
    email_transport.py
    journal.py
    relay_transport.py   # stub only in this phase
```

Suggested minimal objects:

- `NormalizedStatusPayload`
  - thin normalized view of current task/run/question/artifact data
  - should stay small in the first round; do not invent a large block AST yet
- `RenderedStatusContent`
  - rendered `subject`
  - rendered `text_fallback`
  - rendered `html`
  - attachment / external-delivery side data needed by later stages
- `TaskRunPacket`
  - business payload only
  - `packet_id`
  - `task_id`
  - `created_at`
  - `message_kind`
  - `content_format`
  - `html`
  - `text_fallback`
  - `parent_packet_id`
  - `state_patch`
  - `client_trace_id`
- `OutboundDispatchRequest`
  - transport-facing wrapper around `TaskRunPacket`
  - keeps `to_addr`, `subject`, `in_reply_to`, `references`, and extra headers **outside** the packet
  - this avoids polluting `TaskRunPacket` with mail-only routing fields
- `TransportReceipt`
  - `success`
  - `transport_name`
  - `transport_message_id`
  - `sent_at`
  - `error_message`
- `DeliveryAttempt`
  - one journal row per send attempt

## Implementation Phases

### Phase 0: Extract One Outbound Workflow Shell

Intent:

- remove the large outbound composition block from `mail_runner/app.py`,
- but keep behavior byte-for-byte compatible.

Work:

- create one orchestration module, for example `mail_runner/outbound/service.py`,
- move the current outbound composition flow there,
- keep `reporter.py`, `mail_io.py`, `artifact_resolver.py`, and `external_delivery.py` APIs unchanged.

Recommended file touches:

- `mail_runner/app.py`
- `mail_runner/outbound/__init__.py`
- `mail_runner/outbound/service.py`
- selected `tests/test_app_phase3.py` / `tests/test_app_phase6.py` coverage if helper seams change

Done when:

- `app.py` no longer directly composes subject + bodies + attachments + send in one inline block,
- but visible outbound behavior is unchanged.

Paired execution checklist:

- `docs/plans/pc_outbound_layering_first_slice_checklist.md`

### Phase 1: Extract The Render Boundary

Intent:

- make renderer ownership explicit without changing the frozen mail contract.

Work:

- add `ResultNormalizer` / `NormalizedStatusPayload` as a thin wrapper over current inputs,
- add `HtmlRenderer` / `RenderedStatusContent` as a facade over the existing reporter path,
- keep `mail_runner/reporter.py` as the implementation core in the first round,
- move subject/plain/html generation behind the new renderer facade.

Recommended file touches:

- `mail_runner/outbound/normalizer.py`
- `mail_runner/outbound/renderer.py`
- `mail_runner/reporter.py`
- `tests/test_reporter.py`
- one app-level send-path regression test

Done when:

- the outbound workflow asks the renderer layer for rendered content,
- `app.py` / workflow no longer assemble Markdown/plain/html directly.

Paired execution checklist:

- `docs/plans/pc_outbound_layering_first_slice_checklist.md`

### Phase 2: Insert The Packet Stage

Intent:

- create the future transport handoff boundary without changing transport behavior yet.

Work:

- add `TaskRunPacket`,
- add `TaskRunPacketBuilder`,
- add `PacketIdGenerator`,
- add `OutboundDispatchRequest`,
- build the packet from rendered HTML/text plus stable metadata,
- keep subject and reply headers outside the packet.

Recommended file touches:

- `mail_runner/outbound/contract.py`
- `mail_runner/outbound/packet_builder.py`
- `mail_runner/outbound/service.py`
- `tests/test_models.py` or new packet-focused tests

Done when:

- the main outbound path creates a `TaskRunPacket` before any transport call,
- and transport no longer receives ad hoc body fields assembled in `app.py`.

### Phase 3: Extract The Email Transport

Intent:

- make email one transport implementation instead of the implicit default behavior.

Work:

- add `OutboundTransport`,
- add `EmailTransport`,
- add `OutboundDispatcher`,
- move the actual send step behind `dispatcher.send(dispatch_request)`,
- keep `_store_outgoing_mail(...)` and `_prune_previous_status_mails(...)` in the workflow until receipt semantics settle.

Recommended file touches:

- `mail_runner/outbound/dispatcher.py`
- `mail_runner/outbound/email_transport.py`
- `mail_runner/mail_io.py`
- `mail_runner/outbound/service.py`
- `tests/test_mail_io.py`
- new transport wrapper tests if needed

Done when:

- email transport sends packet content and routing headers,
- but does not build business HTML or invent subject content.

### Phase 4: Add Receipt And Journal

Intent:

- make transport attempts observable and prepare for later transport comparison work.

Work:

- add `TransportReceipt`,
- add `OutboundJournal`,
- add `DeliveryAttempt`,
- write one journal row per send attempt,
- keep journaling local and file-based in the first round.

Recommended file touches:

- `mail_runner/outbound/journal.py`
- `mail_runner/outbound/contract.py`
- `mail_runner/outbound/service.py`
- journal-focused tests

Done when:

- every send attempt yields a receipt,
- every send attempt can be journaled without touching the outbound contract.

### Phase 5: Add Relay/VPS Stub Only

Intent:

- leave a clean extension point for the next stable-connection workstream,
- without starting protocol work early.

Work:

- add `relay_transport.py` stub,
- optionally add config wiring or dispatcher registration seam,
- do **not** implement real remote connection logic in this phase.

Done when:

- future relay/VPS work can start by implementing a transport module,
- not by reopening renderer or app composition layers.

## Suggested File Order

1. `mail_runner/outbound/__init__.py`
2. `mail_runner/outbound/service.py`
3. `mail_runner/outbound/normalizer.py`
4. `mail_runner/outbound/renderer.py`
5. `mail_runner/outbound/contract.py`
6. `mail_runner/outbound/packet_builder.py`
7. `mail_runner/outbound/dispatcher.py`
8. `mail_runner/outbound/email_transport.py`
9. `mail_runner/outbound/journal.py`
10. `mail_runner/outbound/relay_transport.py`

## Test Strategy

Phase-by-phase validation should stay small and additive.

Recommended order:

1. `tests/test_reporter.py`
2. `tests/test_mail_io.py`
3. `tests/test_external_delivery.py`
4. representative outbound app-flow tests from `tests/test_app_phase3.py`, `tests/test_app_phase6.py`, and `tests/test_app_phase6_multi_question.py`

When the main outbound path is switched to the new workflow/dispatcher layers, run the full suite:

- `.venv\Scripts\python.exe -m pytest`

## Explicit Deferrals

- Android protocol changes
- Android repository / cache work
- HTML contract redesign
- summary-first plain-text rewrite
- subject-shape cutover
- neutral outbound model convergence beyond the minimal packet boundary needed here
- real relay/VPS transport implementation
- service hosting / runtime hardening workstreams unrelated to outbound layering

## Exit Criteria

This refactor slice is successful when all of the following are true:

1. `mail_runner/app.py` no longer mixes render, packet, and transport details in one inline block.
2. The main outbound path builds a `TaskRunPacket` before transport.
3. `EmailTransport` only sends payload plus routing headers; it does not define business message shape.
4. Current email-visible behavior remains compatible with the frozen outbound contract.
5. Android can continue in parallel without any protocol update from this refactor.
6. Future stable-connection work can start by implementing a new transport instead of rewriting the current outbound path.
