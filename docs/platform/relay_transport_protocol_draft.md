# Relay Transport Protocol Draft

## Status

- Date: 2026-03-20
- Scope: future platform-facing MVP draft for the first PC -> VPS relay transport
- Layer: Layer 3 platform document
- Related repository plans:
  - `docs/plans/vps_relay_bootstrap_plan.md`
  - `docs/plans/pc_outbound_layering_refactor_plan.md`

2026-03-21 note:

This draft remains useful as reference for the earlier TLS-backed `PC -> VPS` transport line.
It is no longer the active planning authority for the newly chosen public-IP plaintext Android direct-connect path, which
is now governed by `docs/plans/android_pc_vps_evolution_authority.md` and the related Phase 0-1 planning docs.

## Purpose

Define the smallest protocol contract needed to let the current PC-side runner talk to a VPS relay without reopening the finished outbound layering work.

This draft is not yet the current repository behavior.

## Non-Goals

- It does not change the Android-facing contract.
- It does not change the existing mail subject/plain/html format.
- It does not move task execution from the PC to the VPS.
- It does not define a complete long-term platform API.

## MVP Direction

Recommended first connection direction:

- the PC client initiates the connection to the VPS,
- the transport runs over `TLS`,
- the first candidate transport is `WebSocket`,
- `HTTPS long-poll` remains an acceptable fallback only if WebSocket bring-up is blocked.

The VPS should not need to dial back into the PC.

## External Endpoint Requirements

For a stable Android-capable public relay endpoint, the transport requirements are stricter than a local PC-only
loopback or a workstation test with relaxed trust settings.

Required direction:

- the canonical public relay entry should be `wss://<relay-host>/relay`
- the canonical diagnostic entry should be `https://<relay-host>/healthz`
- `wss` and `https` must share the same external TLS identity and trust path
- the public host should be a stable DNS name, not a bare VPS IP, unless the deployment intentionally provisions a
  certificate that Android and other stock clients can validate for that IP
- the certificate chain must be trusted by stock Android clients without custom local CA installation or disabled
  verification

Do not treat the following as production-ready Android relay bring-up:

- raw `ws://` or `http://` against a TLS-enabled public relay port
- self-signed or privately rooted certs that require client-side trust bypass
- split-brain config where `healthz` succeeds on one host/scheme but `relay` upgrades on another

Practical implication for the first Android bootstrap:

- if Android fails with certificate trust errors while the PC can still connect through insecure/manual trust paths, the
  server-side TLS deployment should be treated as incomplete rather than pushing insecure trust exceptions into Android
  as the default path
- debug-only insecure Android probing may still be useful for short-lived bring-up, but it should remain explicitly
  temporary and should not redefine the stable endpoint requirements above

## Existing Repository Inputs

The relay should consume the existing internal outbound split:

- rendered content comes from the renderer layer,
- payload truth comes from `TaskRunPacket`,
- routing truth comes from `OutboundDispatchRequest`.

The relay protocol must not redefine those layers in the first round.

## Recommended MVP Message Set

### 1. `hello`

Sent by the PC immediately after connect.

Minimum fields:

- `message_type`
- `client_id`
- `client_version`
- `transport_token_id`
- `sent_at`

### 2. `hello_ack`

Sent by the relay after auth + session admission.

Minimum fields:

- `message_type`
- `connection_id`
- `server_time`
- `heartbeat_seconds`

### 3. `packet`

Sent by the PC to push one outbound unit into the relay path.

Minimum fields:

- `message_type`
- `packet_id`
- `client_trace_id`
- `task_run_packet`
- `dispatch_metadata`
- `sent_at`

`task_run_packet` should map to the existing repository packet shape.

`dispatch_metadata` should carry only the transport-facing fields:

- `to_addr`
- `subject`
- `in_reply_to`
- `references`
- `headers`

### 4. `packet_ack`

Sent by the relay after it durably accepts or rejects the packet.

Minimum fields:

- `message_type`
- `packet_id`
- `accepted`
- `receipt_id`
- `error_message`
- `received_at`

### 5. `ping`

Sent by either side as a heartbeat.

Minimum fields:

- `message_type`
- `sent_at`

### 6. `error`

Sent when the relay must reject a message or close the session intentionally.

Minimum fields:

- `message_type`
- `code`
- `message`
- `sent_at`

## Auth Baseline

The MVP should use one explicit transport token.

Recommended first-round shape:

- one pre-shared token per development environment,
- loaded from server and PC config,
- validated during the first handshake,
- not embedded in packet payloads after the connection is established.

Do not use mailbox credentials as relay auth.

## Reliability Baseline

The MVP must define:

- at-least-once packet submission from the PC side,
- packet idempotency by `packet_id`,
- explicit `packet_ack`,
- reconnect with client-side resend of unacked packets.

Do not design multi-node delivery semantics in the first round.

## Attachment Baseline

The MVP relay should not invent a second artifact truth layer.

Recommended first-round rule:

- packet body continues to carry `text/plain` and `text/html`,
- attachment/artifact strategy stays aligned with the existing repository truth,
- large-file handling remains a later extension unless already externalized before relay submission.

## Open Decisions Before Coding

1. Is the MVP transport `WebSocket` or `HTTPS long-poll`?
2. Does the relay need durable packet storage on day one, or is memory-backed acceptance enough for the first loopback slice?
3. Will the first relay transport deliver only to another controlled client, or also to a user-facing channel immediately?
4. Does the first PC client keep email as a live fallback during relay rollout? Recommended answer: yes.

## Success Condition

This draft is good enough to support coding when:

- the repository server bootstrap can implement the listed message types without inventing new business payload shapes,
- the current outbound layering seam can feed the relay directly,
- and the first VPS implementation can be built without changing Android-facing protocol documents.

For public Android rollout readiness, an additional stability condition also applies:

- the published relay endpoint must satisfy the external TLS/WSS requirements in this document so Android can complete
  `https /healthz` and `wss /relay` without custom trust bypass.
