# VPS Relay Bootstrap Plan

## Status

- Date: 2026-03-21
- Scope: repository-scoped bootstrap plan for the first VPS-backed relay workstream in `mail_based_task_manager`
- Layer: Layer 2 repository plan
- Implementation status:
  - Phase A local skeleton is now landed in `mail_runner/relay_server/`.
  - Phase B local protocol loopback is now landed in code behind the relay transport seam.
  - Phase C VPS bootstrap is now landed: the relay service is deployed, survives restart, and the current PC can reach `/healthz` on the VPS over public `:8787`.
- Phase D repository-side transport wiring is now landed in code and tests: outbound config can select `relay`, the client can speak remote WebSocket relay, and local `email` remains available as fallback.
- Phase E repository-side persistence and fallback hardening is now landed in code and tests: relay packet/session continuity is durable on the VPS side, delivery attempts are recorded durably, and relay failure can fall back to direct `email` when configured.
- Live VPS rollout verification for the new Phase D-E path is now partially established at the bootstrap level.
- 2026-03-20 Android bootstrap smoke captured a certificate-trust problem on the then-current TLS deployment.
- 2026-03-21 live cutover note: the inspected VPS now serves the chosen public plaintext baseline on
  `124.223.41.153:8787`.
- 2026-03-21 note: this plan remains useful for repository-side relay bootstrap and low-level server work, but it is
  no longer the active cross-repo Android main-path authority after the public-IP plaintext direct-connect decision.
- Related docs:
  - `docs/plans/pc_outbound_layering_refactor_plan.md`
  - `docs/plans/vps_environment_baseline.md`
  - `docs/platform/relay_transport_protocol_draft.md`

## Goal

Start the server-side work without reopening the completed outbound layering refactor.

The immediate goal is:

1. keep the current Android-facing mail contract frozen,
2. keep the current email transport working as the production fallback,
3. add the minimum server-side relay skeleton needed to validate a stable PC -> VPS connection path.

## Scope Boundary

This plan is intentionally narrow.

In scope:

- repository-local server module layout,
- first relay service entrypoint,
- local development loop for the relay,
- first VPS deployment baseline,
- transport wiring from the existing outbound seam to the future relay seam.

Out of scope:

- Android protocol changes,
- mail HTML/plain-text redesign,
- full remote task execution on the server,
- database-backed multi-tenant platform work,
- production-grade auth hardening beyond the MVP needed for controlled development.

## Product Decision

For the first VPS slice, the server should act only as a `relay/control plane`.

It should not initially own:

- mail parsing truth,
- workspace execution,
- scheduler authority,
- artifact truth,
- task lifecycle semantics that already live on the PC side.

That keeps the new server line aligned with the completed render / packet / transport split.

Current concrete repository decisions for the first D-E landing:

- transport: `WebSocket` (`ws` locally, `wss` when TLS cert/key are configured on the VPS)
- delivery target: VPS sends the user-facing mail directly through its own SMTP config
- continuity scope: VPS keeps durable relay history plus session continuity, but does not become task-execution truth
- failure policy: relay may automatically fall back to direct PC-side `email` transport when explicitly configured to do so

## Historical TLS Observation From 2026-03-20

Focused Android bootstrap validation on 2026-03-20 exposed a certificate-trust problem on the then-current TLS
deployment:

- the relay service itself was reachable
- the provided relay transport token matched the server-side configured token id
- the public endpoint reported `tls_enabled = true`
- stock Android did not trust the presented certificate chain

Those observations are now historical rather than active rollout requirements.

After the 2026-03-21 cutover, the current cross-repo authority is the public-IP plaintext baseline in
`docs/plans/phase0_public_plaintext_baseline.md`, not the older TLS-hostname-trust path.

User-facing `/status` query boundary for the relay path:

- the preferred running-session reply remains a simple user-facing mail that says the session is running and shows the latest assistant-visible output,
- if no assistant output exists yet, the running-session reply should still stay short and explicitly say that no assistant output is available yet,
- this is a good fit for relay because the Android-facing mail contract stays unchanged and the VPS only needs to deliver the already-rendered reply,
- this reply shape does not make the VPS authoritative for PC execution health,
- if Android needs visibility while the PC host is offline, stuck, or has not consumed the `/status` mail yet, the repository should add a separate VPS-visible last-known-health / heartbeat projection instead of overloading the user-facing running `/status` reply.

## Recommended Repository Shape

Keep the server work in this repository for now.

Recommended initial layout:

```text
mail_runner/
  relay_server/
    __init__.py
    app.py
    auth.py
    config.py
    protocol.py
    session_store.py
```

The server remains a repository-local module until it develops a truly separate release and operations lifecycle.

## First Implementation Decision

Start with a Python-first relay service.

Reason:

- the current repository is already Python-first,
- the VPS baseline already has Python 3.12 installed,
- the first objective is transport validation, not cross-language architecture,
- this keeps the first deployment smaller than introducing Docker + reverse proxy + sidecars on day one.

`Node` should remain optional until a concrete server-side need appears.

## Execution Phases

### Phase A: Local Skeleton

Deliver:

- `mail_runner/relay_server/app.py`
- a minimal local config shape
- a no-op or memory-backed session store
- a first protocol parser/serializer stub

Done when:

- the relay can boot locally,
- expose one health endpoint or one local socket endpoint,
- and log connection lifecycle cleanly.

Current repo status:

- landed: `mail_runner/relay_server/app.py`
- landed: `mail_runner/relay_server/config.py`
- landed: `mail_runner/relay_server/auth.py`
- landed: `mail_runner/relay_server/protocol.py`
- landed: `mail_runner/relay_server/session_store.py`
- landed: focused local tests for config/auth/protocol/session/health endpoint
- landed: actual PC -> local relay handshake and packet loopback now continue in Phase B

### Phase B: Protocol Loopback

Deliver:

- a development-only PC -> local relay handshake,
- transport auth token loading,
- one `hello` + `packet` + `ack` happy path.

Done when:

- the current PC process can push one existing outbound packet shape into the local relay path,
- without changing the user-visible mail contract.

Current repo status:

- landed: `mail_runner/relay_server/loopback.py`
- landed: `mail_runner/relay_server/packet_store.py`
- landed: protocol support for `hello_ack`, `packet_ack`, and server-side error parsing
- landed: `mail_runner/outbound/relay_transport.py` can now run a local `hello -> packet -> ack` loopback when explicitly configured
- landed: focused tests for loopback auth, idempotent packet acceptance, and dispatcher/transport integration
- pending: broader live packet-path exercise beyond the bootstrap handshake

### Phase C: VPS Bootstrap

Deliver:

- VPS-side checkout or deploy path,
- Python venv bootstrap,
- systemd unit,
- one documented start/stop/status path.

Done when:

- the relay boots on the current Ubuntu VPS,
- survives restart,
- and can accept a test connection from the PC side.

Current repo/runtime status:

- landed: `mail_runner/relay_server/deploy.py`
- landed: `scripts/deploy_relay_server.py`
- landed: `docs/plans/vps_relay_deploy_runbook.md`
- verified: `mail-runner-relay.service` is active on the inspected Ubuntu VPS
- verified: remote `curl http://127.0.0.1:8787/healthz` returns `status=ok`
- verified: PC-side health check succeeds through an SSH local tunnel
- verified: current public `http://124.223.41.153:8787/healthz` returns `status=ok` with `tls_enabled=false`
- verified: current public `ws://124.223.41.153:8787/relay` completes `hello -> hello_ack`
- verified: invalid token on the current public plaintext relay returns `unauthorized`
- verified: `https://124.223.41.153:8787/healthz` and `wss://124.223.41.153:8787/relay` fail after the plaintext cutover

### Phase D: PC Transport Wiring

Deliver:

- a real relay transport implementation behind the existing outbound seam,
- explicit selection between `email` and `relay`,
- no regression to the default email path.

Done when:

- the repository can select relay transport without reopening the render / packet / journal layers,
- and email remains a working fallback.

Current repo/runtime status:

- landed: `AppConfig.outbound_transport` can now choose `email` or `relay`
- landed: `mail_runner/outbound/relay_transport.py` now supports remote WebSocket transport in addition to the earlier local loopback seam
- landed: attached user-facing artifacts are embedded into relay submission with enough materialization data for VPS-side delivery
- landed: relay packet acknowledgements now return the final delivered `Message-ID` so PC-side reply-chain continuity can be preserved locally
- landed: focused local tests cover remote WebSocket runtime, dispatcher selection, and fallback behavior
- pending: live VPS verification of the remote packet endpoint and direct user-facing delivery path on the current
  public plaintext deployment

### Phase E: Hardening Pass

Deliver:

- bounded reconnect policy,
- structured logs,
- transport-level error classification,
- basic firewall and service notes.

Done when:

- the development relay is operationally understandable,
- but before any larger platform redesign starts.

Current repo/runtime status:

- landed: `PersistentSessionStore` and `PersistentAcceptedPacketStore` keep relay/session continuity and delivery attempts across relay restart
- landed: relay delivery failures can be journaled durably and may fall back to direct `email`
- landed: deployment helpers now install Python dependencies and support SMTP/state-dir/TLS env inputs needed by the real relay path
- pending: live firewall/service verification on the inspected VPS for the new packet endpoint
- pending specifically: live packet-path exercise, delivery-path verification, and long-running operational observation on
  the current plaintext endpoint

## Development Guardrails

- Do not move task execution onto the VPS in the first slice.
- Do not create a second truth layer for task state.
- Do not redesign `TaskRunPacket` just because a server now exists.
- Do not block the current email transport while bringing up the relay.
- Keep the first server deployment simple enough to debug over SSH.

## Exit Criteria

This bootstrap plan is complete when:

1. a server module exists in this repository,
2. the relay can be started locally and on the inspected VPS,
3. the relay protocol MVP is written down and implemented consistently,
4. a future real `RelayTransport` can be developed without reopening the completed outbound layering refactor.
