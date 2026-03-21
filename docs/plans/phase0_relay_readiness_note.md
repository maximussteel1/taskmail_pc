# Phase 0 Relay Readiness Note

## Status

- Date: 2026-03-21
- Scope: verified readiness note for the public-IP plaintext direct-connect baseline
- Layer: Layer 2 repository note
- State: verified current-state artifact; suitable for Phase 0 Work Package C closeout
- Related docs:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_execution_plan.md`
  - `docs/plans/android_pc_vps_phase0_phase1_checklist.md`
  - `docs/plans/phase0_public_plaintext_baseline.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-authority-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-phase0-public-plaintext-baseline-v1.md`

## Purpose

Record what the repository can now say about live relay readiness after the direction reset to public-IP plaintext
direct-connect.

This note now answers one concrete question:

> Does the current repository have a reviewable baseline for public plaintext direct connect, and does the live VPS now
> match it?

## Active Intended Baseline

The active planning baseline is frozen in:

- `docs/plans/phase0_public_plaintext_baseline.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-phase0-public-plaintext-baseline-v1.md`

The shared baseline is:

- public IP: `124.223.41.153`
- configured port: `8787`
- diagnostic endpoint: `http://124.223.41.153:8787/healthz`
- connection endpoint: `ws://124.223.41.153:8787/relay`
- token-based bootstrap/auth admission
- explicit mail fallback on bootstrap failure

## What The Repository Already Establishes

### 1. The Relay Server Can Run Without TLS

Repository code already supports intentional plaintext runtime:

- `mail_runner/relay_server/app.py` builds no SSL context when cert and key are not provided
- runtime logging reports `ws` rather than `wss` in that case
- `tests/test_relay_server_config.py` already covers config loading with `tls_certfile=None` and `tls_keyfile=None`

### 2. The Plain Endpoints Already Exist In Code

Repository runtime code already exposes:

- `/healthz`
- `/readyz`
- `/relay`
- bearer-token transport auth for the first handshake

### 3. Deployment Config Can Omit TLS Inputs

Repository deployment helpers already treat TLS cert and key as optional rather than mandatory.

That means the codebase is structurally capable of the chosen plaintext mode.

## Current Workspace Observation

The current workspace has a concrete cross-repo baseline rather than only a candidate target:

- `vps.txt` records public IP `124.223.41.153`
- `docs/plans/phase0_public_plaintext_baseline.md` freezes that IP as the repository-side baseline
- the Android repository freezes the same IP and endpoints in its paired Phase 0 baseline note

## Live Cutover And Verified Probe Result On 2026-03-21

The 2026-03-21 live cutover and probe establish the current VPS state directly:

- SSH access to `ubuntu@124.223.41.153` succeeded
- `/etc/mail-runner-relay.env` was backed up as `/etc/mail-runner-relay.env.20260321_105428.bak`
- `MAIL_RELAY_TLS_CERTFILE` and `MAIL_RELAY_TLS_KEYFILE` were removed from `/etc/mail-runner-relay.env`
- `mail-runner-relay.service` restarted successfully under systemd after the env change
- the deployed env now keeps:
  - `MAIL_RELAY_HOST=0.0.0.0`
  - `MAIL_RELAY_PORT=8787`
- `http://124.223.41.153:8787/healthz` returned `200 OK`
- the HTTP JSON payload reported:
  - `status = ok`
  - `service = mail-runner-relay`
  - `listen.port = 8787`
  - `tls_enabled = false`
  - `auth.transport_token_id = 6f05b17d957d`
- `https://124.223.41.153:8787/healthz` now fails TLS negotiation against the plaintext runtime
- `ws://124.223.41.153:8787/relay` returned `hello_ack` with the live transport token
- `ws://124.223.41.153:8787/relay` with an invalid token returned `error: unauthorized / transport token mismatch`
- `wss://124.223.41.153:8787/relay` now fails TLS negotiation with `wrong version number`

## Current Readiness Judgment

### Ready Enough

- the live VPS now serves the frozen public plaintext baseline
- the live host, port, endpoint shape, and token admission model match the shared Phase 0 target
- public `http /healthz` is directly reachable without TLS bypass
- public `ws /relay` completes `hello -> hello_ack`
- public token rejection behavior is explicit on the plaintext path

### Remaining Follow-Up

- Phase 1 still needs reusable connection-seam, failure-routing, and bootstrap-promotion artifacts

## Current Conclusion

The repository is now past the point of "the chosen public plaintext baseline is only a plan."

It is also past the point of "live readiness is still unresolved."

In other words:

- the codebase can support the chosen mode
- the cross-repo Phase 0 baseline is frozen consistently
- the inspected live VPS now matches that plaintext baseline
- repository-side Phase 0 closeout is now explicit
- remaining work moves from Phase 0 closeout to Phase 1 bootstrap promotion
