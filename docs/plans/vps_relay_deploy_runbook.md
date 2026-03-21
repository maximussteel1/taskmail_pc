# VPS Relay Deploy Runbook

## Status

- Date: 2026-03-20
- Scope: concrete Phase C deployment/runbook for the current lightweight relay bootstrap
- Layer: Layer 2 repository plan
- Related docs:
  - `docs/plans/vps_relay_bootstrap_plan.md`
  - `docs/plans/vps_environment_baseline.md`

## Purpose

Record the exact repository-side deployment path for the current relay skeleton on the inspected Ubuntu VPS.

This runbook is intentionally narrow. It only covers:

- shipping the repository-side relay package plus its Python dependencies,
- creating the VPS venv,
- installing the env file and `systemd` unit,
- starting and checking the relay service.

## Deployment Command

Run from the repository root on the Windows PC:

```powershell
.\.venv\Scripts\python.exe .\scripts\deploy_relay_server.py `
  --host <vps-ip> `
  --user ubuntu `
  --key-path .\work_bot.pem `
  --smtp-host <smtp-host> `
  --smtp-user <smtp-user> `
  --smtp-password <smtp-password> `
  --from-addr <from-addr>
```

Notes:

- If `--transport-token` is omitted, the script generates one and prints only its fingerprint.
- The script now deploys the repository relay package together with `requirements.txt` and installs the Python dependencies in the VPS venv.
- `--state-dir` is optional; by default it resolves to `/opt/mail_runner_relay/shared/state`.
- If `--tls-certfile` and `--tls-keyfile` are supplied, the relay serves `wss` / `https` on the configured port.
- The remote service is installed as `mail-runner-relay`.

## Remote Layout

- Base dir: `/opt/mail_runner_relay`
- Current release symlink: `/opt/mail_runner_relay/current`
- Venv: `/opt/mail_runner_relay/venv`
- State dir: `/opt/mail_runner_relay/shared/state`
- Logs:
  - `/opt/mail_runner_relay/shared/logs/mail-runner-relay.stdout.log`
  - `/opt/mail_runner_relay/shared/logs/mail-runner-relay.stderr.log`
- Env file: `/etc/mail-runner-relay.env`
- Unit file: `/etc/systemd/system/mail-runner-relay.service`

## Service Commands

```bash
sudo systemctl status mail-runner-relay --no-pager
sudo systemctl restart mail-runner-relay
sudo systemctl stop mail-runner-relay
sudo journalctl -u mail-runner-relay -n 100 --no-pager
curl http://127.0.0.1:8787/healthz
```

## Current Verification Snapshot

As of `2026-03-20`, the current inspected VPS has been verified as follows:

- `mail-runner-relay.service` is `active (running)` under `systemd`
- `ss -ltnp` shows the relay listening on `0.0.0.0:8787`
- remote localhost health check passes:
  - `curl http://127.0.0.1:8787/healthz`
- PC-side tunnelled health check passes through:
  - `ssh -L 18787:127.0.0.1:8787 ...`
  - `curl http://127.0.0.1:18787/healthz`
- PC-side direct public health check now passes:
  - `curl http://<vps-ip>:8787/healthz`

With public `8787/tcp` ingress opened, the Phase C bootstrap path is now complete for the current relay skeleton.

Important freshness note:

- the verification snapshot above reflects the original Phase C health-check skeleton on `2026-03-20`
- the newer repository-side Phase D-E path (remote packet endpoint, durable relay history, VPS SMTP delivery, optional TLS) still needs a fresh live verification pass on the inspected VPS after deployment

## Expected Health Shape

`/healthz` should return JSON with:

- `status=ok`
- `service=mail-runner-relay`
- `listen.host`
- `listen.port`
- `session_count`
- `auth.transport_token_id`

## Current Boundary

This runbook now covers the repository-side deployment path for the real relay service, including:

- the remote packet endpoint,
- durable relay/session state under the shared state directory,
- VPS-side SMTP delivery configuration,
- optional TLS cert/key wiring.

What remains outside this document is live acceptance evidence for the upgraded path on the inspected VPS.
