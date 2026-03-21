# Phase 2 Direct Outbound Contract (v1)

## Status

- Date: 2026-03-21
- Scope: first shared Phase 2 direct outbound contract for Android TaskMail
- Layer: Layer 2 cross-repo contract freeze
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase0_direct_connect_handoff.md`
  - `docs/plans/phase1_direct_connect_bootstrap.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-phase2-direct-outbound-contract-v0.1.md`

## Purpose

Freeze one narrow direct outbound slice so Android and PC/VPS can start Phase 2 implementation without reopening the
transport boundary every session.

This note does not change current Layer 1 behavior.
Today, Android still ships mail-first behavior and the current Layer 1 Android contract remains mail control-plane
based.

## Current Reading

- repository-side Phase 1 bootstrap artifacts are already landed
- cross-repo Phase 1 is still not being declared closed by this note
- this note freezes the first Phase 2 wire contract early because the next implementation step is blocked on payload
  shape, fallback rules, and ack semantics

## V1 Scope

The Phase 2 v1 direct action set is intentionally narrow:

- supported direct action:
  - `new_task`
- still mail-only:
  - plain continuation reply
  - `/status`
  - `/pause`
  - `/resume`
  - `/end`
  - direct read-side updates
  - attachment-bearing direct actions

The preferred first direct business slice remains `new task`.
This note freezes only that slice.

## Reuse Boundary

Phase 2 v1 reuses the current relay bootstrap seam:

- public plaintext baseline from Phase 0
- `/healthz`
- `/relay`
- token admission
- `hello -> hello_ack`
- `packet -> packet_ack`
- current packet idempotency behavior

Phase 2 v1 does not reuse the current PC outbound status-delivery packet as an Android business contract.

The distinction is:

- current PC outbound relay packet:
  - delivers already-rendered user-facing task mail
- new Android direct outbound packet:
  - carries one versioned TaskMail action payload over the same relay transport wrapper

This keeps the connection seam reused while avoiding accidental commitment to the current PC-only mail-delivery packet
meaning.

## Transport Sequence

The Phase 2 v1 `new_task` direct path is:

1. Android resolves the saved relay config through the Phase 1 bootstrap manager or equivalent seam.
2. Android may probe `healthz`, but `healthz` remains diagnostic rather than business truth.
3. Android connects to `/relay`.
4. Android sends `hello`.
5. Android requires `hello_ack` before any business packet is sent.
6. Android sends one `packet` carrying the v1 direct action envelope.
7. PC or VPS returns `packet_ack` or `error`.
8. If the packet is accepted, later task progress and results still flow back through the current mail-facing status
   path until Phase 3 changes the read side.

## Packet Wrapper

Phase 2 v1 uses the existing relay `packet` wrapper:

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:new-task:req_20260321_001",
  "client_trace_id": "req_20260321_001",
  "task_run_packet": {
    "schema_version": "phase2-direct-outbound-contract-v1",
    "action": "new_task",
    "request_id": "req_20260321_001",
    "origin": {
      "client": "android_taskmail",
      "sender_account_uuid": "acc-001"
    },
    "new_task": {
      "backend": "codex",
      "repo_path": "E:\\projects\\android_task_manager",
      "workdir": "feature/taskmail/internal",
      "task_text": "Audit the current direct-send handoff path.",
      "subject_title": "Audit the direct-send handoff path",
      "timeout_minutes": 120,
      "mode": "analysis_only",
      "profile": "android",
      "permission": "highest",
      "acceptance": [
        "List any contract mismatches.",
        "Do not change user-facing reply semantics."
      ]
    }
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "phase2-direct-outbound-contract-v1",
    "action": "new_task",
    "fallback_policy": "mail"
  },
  "sent_at": "2026-03-21T12:30:00"
}
```

The relay parser already treats `task_run_packet` and `dispatch_metadata` as generic mappings.
Phase 2 v1 intentionally uses that transport wrapper rather than introducing a second WebSocket endpoint.

## Field Rules

### Wrapper Fields

- `packet_id`:
  - required
  - transport idempotency key
  - recommended shape: `android-taskmail:new-task:<request_id>`
- `client_trace_id`:
  - required
  - should equal `request_id` in v1
- `sent_at`:
  - required
  - client-side ISO timestamp

### `task_run_packet` Envelope

- `schema_version`:
  - required
  - fixed to `phase2-direct-outbound-contract-v1`
- `action`:
  - required
  - fixed to `new_task`
- `request_id`:
  - required
  - stable per user-visible direct-send attempt
- `origin.client`:
  - required
  - fixed to `android_taskmail`
- `origin.sender_account_uuid`:
  - optional
  - provenance only in v1

### `new_task` Payload

- `backend`:
  - required
  - allowed values: `opencode`, `codex`
- `repo_path`:
  - required
  - same business meaning as current `Repo:` in first-mail parsing
- `workdir`:
  - optional nullable string
  - same business meaning as current `Workdir:`
- `task_text`:
  - required
  - same business meaning as current `Task:`
- `subject_title`:
  - required
  - title without `[OC]` or `[CX]`; backend prefix is still derived from `backend`
- `timeout_minutes`:
  - optional nullable positive integer
  - omitted means current repository default timeout still applies
- `mode`:
  - optional
  - allowed values: `modify`, `analysis_only`
  - omitted means `modify`
- `profile`:
  - optional nullable string
- `permission`:
  - optional nullable string
  - allowed values for repository acceptance: `default`, `highest`, or omitted
  - Android v1 is expected to emit only `highest` or omitted
- `acceptance`:
  - required array
  - may be empty
  - each item must be a non-empty string

### `dispatch_metadata`

`dispatch_metadata` is required for routing and operator visibility.
It is not a mail-header block in this contract.

Required fields:

- `channel = taskmail_android_direct`
- `schema_version = phase2-direct-outbound-contract-v1`
- `action = new_task`
- `fallback_policy = mail`

## Business Meaning Freeze

When PC or VPS accepts the v1 `new_task` payload, it must compile to the same business meaning as the current first
task mail path:

- create a fresh thread or session rather than attempting reply reuse
- use the same backend mapping as `[OC]` and `[CX]`
- interpret `repo_path`, `workdir`, `task_text`, `acceptance`, `timeout_minutes`, `mode`, `profile`, and `permission`
  with the same semantics as current first-mail parsing
- keep user-facing status mail subjects, bodies, state capsules, question capsules, and attachment rules on the current
  mail-facing contract

This note freezes transport and business mapping only for direct task creation.
It does not reopen current reply or status semantics.

## Ack Meaning

`packet_ack.accepted = true` means:

- the direct request was accepted into the Phase 2 direct-action lane
- the request idempotency key was persisted strongly enough to avoid duplicate task creation on relay-level retry
- the system may now continue the run and later status reporting asynchronously

`packet_ack.accepted = true` does not mean:

- the backend run already entered `[ACCEPTED]`
- the first status mail already exists
- Android can infer the final TaskMail session id from the ack alone

For v1:

- `receipt_id` is required and should remain stable for repeated relay handling of the same `packet_id`
- `transport_message_id` is optional and Android must not depend on it for v1 UI identity

## Fallback Matrix

| Condition | Expected Android behavior |
| --- | --- |
| relay config missing | fall back to current mail `new task` path |
| `healthz` failure or connect timeout | fall back to current mail `new task` path |
| `hello` does not reach `hello_ack` | fall back to current mail `new task` path |
| connection drops before `packet_ack` | fall back to current mail `new task` path |
| server returns `unsupported_action` or equivalent capability rejection | fall back to current mail `new task` path |
| server returns `direct_temporarily_unavailable` or equivalent transient direct-path rejection | fall back to current mail `new task` path |
| server returns `invalid_payload`, `validation_failed`, `unauthorized`, or equivalent hard rejection | do not silently fall back; keep draft and show direct-send error |
| `packet_ack.accepted = true` | do not send duplicate mail fallback; wait for later status mail |

If Android attempts mail fallback and that mail send also fails, Android should keep the draft and surface the actual
mail-send failure rather than pretending either path succeeded.

## Retry Rule

- relay-level retry before a visible failure should reuse the same `packet_id` and `request_id`
- a fresh user tap after Android has already surfaced a failure may create a new `request_id`
- PC or VPS should treat repeated packets with the same `packet_id` as the same logical direct request

## Explicit Non-Goals

This contract does not yet freeze:

- reply direct payloads
- direct `/status`
- direct pause or resume semantics
- direct read-side state updates
- packet history read APIs
- a new Android-visible endpoint other than the current relay entry

## Current Conclusion

Phase 2 now has one reviewable contract freeze for the first direct action slice.

The next implementation step should use this note to land:

- Android direct `new task` packet serialization and fallback routing
- PC or VPS acceptance of that payload over the existing relay wrapper
- focused tests that prove idempotent acceptance and clean fallback behavior
