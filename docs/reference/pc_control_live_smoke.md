# PC Control Live Smoke

## 目的

这条 smoke 用来补 repo-side fixture 之外的真实联调证据。当前 live 证据分成三层：

- 真实 `ws://.../pc-control` 连接
- `pc_hello -> hello_ack`
- `workspace_snapshot` 进入 relay health 视图
- 同一 `pc_id` 的 reconnect / `stale_connection_epoch` fencing
- live `command_dispatch -> command_ack -> event -> output_chunk -> result`
- live `output_resume_request(after_seq=1)` / selective replay
- live `artifact_manifest`
- live 多 `PC` 定向路由

其中第一层由：

- `.\.venv\Scripts\python.exe .\scripts\pc_control_live_smoke.py --config .\mail_config.bot.relay.local.yaml`

第二层由：

- `.\.venv\Scripts\python.exe .\scripts\pc_control_live_roundtrip_smoke.py --config .\mail_config.bot.relay.local.yaml`

第三层由：

- `.\.venv\Scripts\python.exe .\scripts\pc_control_live_multi_pc_smoke.py --config .\mail_config.bot.relay.local.yaml`

如果需要看 live dispatch / replay / artifact 的实际结果与证据，统一读：

- `docs/reference/pc_control_live_smoke_validation.md`

## 入口

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_smoke.py --config .\mail_config.bot.relay.local.yaml
```

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_roundtrip_smoke.py --config .\mail_config.bot.relay.local.yaml
```

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_multi_pc_smoke.py --config .\mail_config.bot.relay.local.yaml
```

## 当前行为

- 默认从本地 config 读取：
  - `relay_url`
  - `relay_transport_token`
  - `relay_client_id`
  - `relay_client_version`
- 默认派生：
  - `health_url = http(s)://.../healthz`
  - `pc_control_url = ws(s)://.../pc-control`
- 默认不会复用常驻 sidecar 的 `pc_id`，而是使用：
  - `<relay_client_id>-live-smoke-<run-name-suffix>`
  目的：避免把当前 live `pc-home` sidecar 顶成 stale。

## 验证内容

1. `healthz` 可达且返回 `status=ok`
2. 首次 `pc_hello` 得到真实 `hello_ack`
3. 发送 `workspace_snapshot` 后，`/healthz.pc_control` 计数发生符合预期的增量：
   - `node_count +1`
   - `workspace_count +<本次 snapshot 的 workspace 数>`
4. 第二次 `pc_hello` 对同一 probe `pc_id` 得到更高的 `connection_epoch`
5. 使用旧 epoch 发送 heartbeat 时，server 返回 `stale_connection_epoch`

## 输出

结果默认落到：

```text
_tmp_pc_control_live_smoke/<run-name>/smoke_result.json
```

其中会记录：

- `health_before`
- `hello_ack_one`
- `snapshot_observation`
- `hello_ack_two`
- `stale_epoch_observation`
- `remote_state_fetch_error`

## 远端 state 取证

这条 smoke 还支持可选的 SSH 远端 state 取证：

- 默认 SSH user：`ubuntu`
- 默认 key：`.\work_bot.pem`
- 默认远端 state dir：`/opt/mail_runner_relay/shared/state/pc_control`

如果 SSH 读取 `pc_nodes.json` / `workspaces.json` 成功，脚本会优先使用远端 state 作为更强证据；如果 SSH 超时或失败，则会回退到 `/healthz` 增量证据，不会把整条 smoke 直接判为失败。

## 当前边界

这条 smoke 当前已经关闭了单 PC live 的：

- single-PC live websocket bring-up
- live `workspace_snapshot` 可见性
- live reconnect fencing
- live `command_dispatch -> command_ack -> event -> result -> output_chunk`
- live `output_resume_request` / selective replay
- live `artifact_manifest` 真机往返
- live 多 `PC` 定向路由

当前还没关闭：

- 更高层多 `PC` observer / subscription 侧证据
