# `vps_only` Checkpoint 恢复验证 Runbook（v0.1）

## Status

- Date: 2026-03-27
- Scope: repository-side `control_plane_mode=vps_only` checkpoint 的恢复服务与集中验证入口
- Layer: planning / operator runbook
- Source of truth:
  - `docs/current/mail_protocol.md`
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`

## 目的

这份 runbook 只回答两件事：

- 当服务在 `vps_only` checkpoint 上被故意保持离线后，下一次如何恢复
- 恢复后最小需要验证哪些事实，才能说明 checkpoint 仍然成立

它不负责继续扩展协议，也不负责决定是否立即删除 `COS` / mail 兼容实现。

## 单入口

repo-side 现在已有一个把本 runbook 最小验证矩阵串起来的单入口：

```powershell
.\.venv\Scripts\python.exe .\scripts\vps_only_checkpoint_validation.py --config .\mail_config.bot.relay.local.yaml
```

这条入口当前会统一输出一份 JSON 结果，至少覆盖：

- `GET /healthz`
- `GET /debug/pc-control/nodes`
- `GET /debug/pc-control/workspaces`
- relay `/v1/files` upload + metadata/content roundtrip
- 旧 direct `new_task` 在当前 relay 上返回 `unsupported_action`
- 当前 task-root 的 external-delivery observation window 是否已达到 `window_ready`

## 最新 live rerun（2026-03-27）

本 runbook 对应的最新一次真实 bring-up / rerun 已完成，当前应这样读：

- 初始排查时，VPS `mail-runner-relay.service` 处于 `inactive (dead)`，远端 `127.0.0.1:8787` 与公网 `124.223.41.153:8787` 都拒连
- 通过现有 runbook 的远端 `sudo systemctl start mail-runner-relay` 恢复后，relay `/healthz` 重新返回 `200`
- 本机 host 与 relay task-root sync companion 也已恢复到 `running`
- 随后的真实 rerun 已成功通过最小 owner-seam 验证，结果路径：
  - `_tmp_vps_only_checkpoint_validation/vps-only-checkpoint-validation-20260327_live_rerun/validation_result.json`
  - `_tmp_vps_only_checkpoint_validation/vps-only-checkpoint-validation-20260327_live_rerun/artifact_contract_smoke/vps-only-checkpoint-validation-20260327_live_rerun-artifact-contract/smoke_result.json`

这次 live rerun 具体确认了：

- `healthz.status=ok`
- `taskmail_direct_ingress_enabled=false`
- `pc-home` 在线，且 `workspace_count=23`
- live `/v1/files` smoke 的 `metadata_status=200`、`download_status=200`、`download_verified=true`
- 旧 direct `new_task` 继续稳定返回 `unsupported_action`

当需要更细粒度拆开排障时，再回到下面的分项命令。

## 当前 checkpoint 读法

截至 `2026-03-27`，当前 checkpoint 应按下面这些事实读取：

- 本机 live host config 已切到 `control_plane_mode=vps_only`
- VPS relay deploy env 已显式带 `MAIL_RUNNER_CONTROL_PLANE_MODE=vps_only`
- PC host 不再 consume bot mailbox 作为控制面入口
- relay `/healthz` 在该模式下应返回 `taskmail_direct_ingress_enabled=false`
- 旧 direct `new_task` / current-session `status|reply` / bootstrap `[SYNC] v1` / `transport_probe` mail harness 不再应读成 active lane
- `pc-control`、relay `/v1/files`、bootstrap `[SYNC] v2`、Android-facing `POST /v1/android/create-session` 仍可保留为 active seam

## 恢复服务顺序

建议按这个顺序 bring-up：

1. 先确认本机仓库工作树和目标 release 没有 operator 未预期的漂移。
2. 先启动 VPS relay，再启动本机 host。
3. 本机 host 启动后，确认 relay task-root sync companion 也恢复。
4. 先跑最小 owner-seam 验证，再决定是否继续观察窗口。

### 1. 启动 VPS relay

若 repo-side 代码有变化，先按当前 deploy 入口重部署：

```powershell
.\.venv\Scripts\python.exe .\scripts\deploy_relay_server.py `
  --host 124.223.41.153 `
  --user ubuntu `
  --key-path .\work_bot.pem `
  --control-plane-mode vps_only `
  --task-root /opt/mail_runner_relay/shared/task_root `
  --smtp-host <smtp-host> `
  --smtp-user <smtp-user> `
  --smtp-password <smtp-password> `
  --from-addr <from-addr> `
  --transport-token <transport-token> `
  --android-app-token <android-app-token>
```

若不需要重部署，只需远端启动服务：

```bash
sudo systemctl start mail-runner-relay
sudo systemctl status mail-runner-relay --no-pager
curl http://127.0.0.1:8787/healthz
```

### 2. 启动本机 host

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 start -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

启动后立刻确认：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

## 最小验证矩阵

恢复后至少应确认以下事实：

### A. relay health 语义

- `GET /healthz` 返回 `status=ok`
- `taskmail_direct_ingress_enabled=false`
- `task_root.scheduler_present=true`

### B. `pc-control` seam

- `GET /debug/pc-control/nodes` 能看到 `pc-home` 在线
- `current_connection_epoch` 正常递增
- `workspace_count` 为非零

### C. `/v1/files` seam

- `POST /v1/files` 成功
- `GET /v1/files/{file_id}` 返回 `200`
- `GET /v1/files/{file_id}/content` 返回 `200`
- 回读内容与上传内容一致

### D. 旧 mail-bridge surface 已关闭

- 对旧 direct `new_task` 做一次定点 packet probe
- 期望返回 `unsupported_action`
- 不应再把 bridge SMTP/env 凭据误读成“旧 direct ingress 仍可用”

## 建议验证命令

### relay health / pc-control

```powershell
.\.venv\Scripts\python.exe -c "import requests, json; h={'Authorization':'Bearer <transport-token>'}; print(json.dumps(requests.get('http://124.223.41.153:8787/healthz', timeout=20).json(), ensure_ascii=False)); print(json.dumps(requests.get('http://124.223.41.153:8787/debug/pc-control/nodes', headers=h, timeout=20).json(), ensure_ascii=False))"
```

### `/v1/files` roundtrip

```powershell
.\.venv\Scripts\python.exe -c "import json,hashlib,requests; content=b'vps-only-reenable-smoke'; digest=hashlib.sha256(content).hexdigest(); metadata={'artifact_id':'artifact-vps-only-reenable','name':'smoke.txt','kind':'file','role':'attachment','mime_type':'text/plain','byte_size':len(content),'sha256':digest}; h={'Authorization':'Bearer <transport-token>'}; r=requests.post('http://124.223.41.153:8787/v1/files',headers=h,files={'metadata':(None,json.dumps(metadata),'application/json'),'file':('smoke.txt',content,'text/plain')},timeout=30); p=r.json(); c=requests.get('http://124.223.41.153:8787'+p['artifact']['download_url'],headers=h,timeout=30); print(json.dumps({'upload_status':r.status_code,'download_status':c.status_code,'content_text':c.text,'file_id':p.get('file_id')},ensure_ascii=False))"
```

### 旧 direct `new_task` 拒绝探针

建议复用现有小脚本片段，发送一条 `phase2-direct-outbound-contract-v1` `new_task` packet 到 `/relay`，期望响应：

- `message_type=error`
- `code=unsupported_action`
- `message=direct TaskMail action is not available on this relay`

## 观察窗口

若恢复服务后不只做一次 smoke，而是要进入真正 cutover 观察窗口，接着做：

```powershell
.\.venv\Scripts\python.exe .\scripts\external_delivery_window_report.py --config .\mail_config.bot.relay.local.yaml --limit-runs 20 --output .\_tmp_live_mail_runner\external_delivery_window_report_<date>.json
```

观察窗口重点不再是“再证明一次骨架能不能跑”，而是确认：

- `file_surface` 是否在真实 run 中稳定成为默认 provider
- `provider=cos` 是否只继续出现在 oversize 样本上
- `artifact_manifest.download_ref_source` 是否稳定来自 `external_delivery_index.file_surface`

截至 `2026-03-27` 的当前 live 结果是：

- `external_delivery_window_report_20260327_clean_gate.json` 已通过，说明当前 deployment 已达到 `window_ready=true`
- `external_delivery_window_report_20260327_cos_gate.json` 仍失败，当前唯一阻塞是观察窗口里仍保留两条 oversize `COS` delivery
- 因此这次 checkpoint 之后的正确读法是：当前 deployment 已经 cutover-ready，但还不是 `COS` decommission-ready`

## 若要再次保持离线

本机：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 stop -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

VPS：

```bash
sudo systemctl stop mail-runner-relay
sudo systemctl is-active mail-runner-relay
```

## 一句话结论

**这份 runbook 的目标不是继续开发，而是把 `vps_only` checkpoint 的“恢复服务 -> 验证 owner seam -> 再决定是否进入观察窗口”固定成单一入口。**
