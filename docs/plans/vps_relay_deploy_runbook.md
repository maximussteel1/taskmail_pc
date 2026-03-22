# VPS Relay Deploy Runbook

## Status

- Date: 2026-03-21
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
- 如果要让 Phase 3 `subscribe_session_detail` / `session_update` 看到真实 live task store，还需要给 relay 提供可见的 `task_root`：
  - deploy 时传 `--task-root /opt/mail_runner_relay/shared/task_root`
  - 这会在远端 env 中写入 `MAIL_RUNNER_TASK_ROOT`
- To enable the current Phase 2 v1 direct TaskMail `new_task` ingress, also pass:
  - `--taskmail-bot-mailbox-addr <bot-mailbox-addr>`
  - `--taskmail-direct-from-addr <user-mailbox-addr>`
  - optional: `--taskmail-direct-from-name <display-name>`
  - if the shared relay SMTP account cannot send mail with the user mailbox in `From:`, also pass:
    - `--taskmail-direct-smtp-host <smtp-host>`
    - `--taskmail-direct-smtp-port <smtp-port>`
    - `--taskmail-direct-smtp-user <smtp-user>`
    - `--taskmail-direct-smtp-password <smtp-password>`

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

As of `2026-03-22`, a fresh public partial probe now also reconfirms:

- `http://124.223.41.153:8787/healthz` still returns `200 OK`
- the live health payload still reports `tls_enabled = false`
- the live health payload now also exposes `taskmail_direct_ingress_enabled = true`
- `ws://124.223.41.153:8787/relay` with an invalid token still returns `unauthorized / transport token mismatch`

This `2026-03-22` probe is still intentionally partial:

- it does not yet re-close a fresh valid-token `hello -> hello_ack`
- it does not yet re-close remote packet acceptance on the current upgraded path
- it does not yet re-close VPS SMTP delivery on the current upgraded path

## Phase 3 Task Root Visibility

当前 VPS relay 只能读取它本机可见的 `task_root`。如果远端没有这份状态落盘，`subscribe_session_detail` 会因为无法 resolve `thread_state` / `session_state` 而返回 `session_not_found`。

推荐的当前仓库侧做法是：

1. deploy relay 时显式指定远端 task root：

```powershell
.\.venv\Scripts\python.exe .\scripts\deploy_relay_server.py `
  --host <vps-ip> `
  --user ubuntu `
  --key-path .\work_bot.pem `
  --task-root /opt/mail_runner_relay/shared/task_root `
  ...
```

2. 把本地 live task store 同步到这个远端目录：

```powershell
.\.venv\Scripts\python.exe .\scripts\sync_relay_task_root.py `
  --host <vps-ip> `
  --user ubuntu `
  --key-path .\work_bot.pem `
  --local-task-root E:\projects\mail_based_task_manager\_tmp_live_mail_runner\tasks `
  --remote-task-root /opt/mail_runner_relay/shared/task_root
```

3. 如果要支撑 smoke 期间持续变化的 live state，可以把同步脚本跑成轮询模式：

```powershell
.\.venv\Scripts\python.exe .\scripts\sync_relay_task_root.py `
  --host <vps-ip> `
  --user ubuntu `
  --key-path .\work_bot.pem `
  --local-task-root E:\projects\mail_based_task_manager\_tmp_live_mail_runner\tasks `
  --remote-task-root /opt/mail_runner_relay/shared/task_root `
  --repeat-seconds 2
```

## Expected Health Shape

`/healthz` should return JSON with:

- `status=ok`
- `service=mail-runner-relay`
- `listen.host`
- `listen.port`
- `session_count`
- `packet_count`
- `tls_enabled`
- `taskmail_direct_ingress_enabled`
- `auth.transport_token_id`

For the current Phase 2 v1 Android `new_task` smoke path, operator preflight should explicitly confirm:

- `taskmail_direct_ingress_enabled=true` when Android is expected to use direct first-send
- `tls_enabled=true` only when the Android client is configured to use TLS for the same relay endpoint
- health passing alone is not enough; direct-ingress-disabled health should be treated as a mail-fallback scenario, not
  a direct-send-ready relay

## Current Boundary

This runbook now covers the repository-side deployment path for the real relay service, including:

- the remote packet endpoint,
- durable relay/session state under the shared state directory,
- VPS-side SMTP delivery configuration,
- optional TLS cert/key wiring.

What remains outside this document is live acceptance evidence for the upgraded path on the inspected VPS.
