# PC Control-Plane Fixture Smoke 验证结果

## 目的

本文记录 PC control-plane fixture smoke 当前已经实际测到什么、证据在哪，以及哪些缺口已被显式记录。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py
```

结果：

- 状态：成功
- smoke 类型：fixture harness，不调用真实 relay websocket
- 已覆盖消息链路：
  - `pc_hello -> hello_ack`
  - `workspace_snapshot`
  - `command_dispatch -> command_ack(accepted)`
  - `command_dispatch -> command_ack(accepted_but_queued)`
- 已覆盖拒绝 / fencing：
  - `unsupported_backend`
  - `stale_connection_epoch`
- 清理检查：通过
  说明：这条 smoke 不拉起外部进程或监听端口，结果文件显式记录 `cleanup.required=false`

## 当前已确认结论

1. 当前仓库已经有可运行的最小 PC control-plane skeleton，不再只有文档。
2. `command_dispatch -> command_ack` 这条最小链路当前已能区分 `accepted` 与 `accepted_but_queued`。
3. `unsupported_backend` 当前会在 server runtime 侧被显式拒绝，而不是静默降级。
4. `connection_epoch` fencing 当前已经工作，旧连接的 heartbeat 会收到 `stale_connection_epoch`。
5. 当前 capability 虽然已经宣称 `artifact_manifest=true`，但 control-plane packet 层还没有对应 canonical message。

## 证据

- `_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_023348/smoke_result.json`

## 当前未覆盖项

以下内容还没有纳入这条 fixture smoke：

- 真实 websocket 往返
- canonical `event` packet
- canonical `result` packet
- canonical `output_chunk` packet
- canonical `artifact_manifest` packet
- 多 `PC` 路由与订阅

因此，这轮结论目前只覆盖：

- 单机本地 in-memory control-plane loopback
- 当前已实现的 `hello / snapshot / dispatch / ack / fencing` 骨架
- 明确的 gap 记录，而不是完整 Phase 1 联调完成
