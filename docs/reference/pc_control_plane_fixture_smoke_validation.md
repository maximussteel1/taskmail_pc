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
.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py --run-name pc-control-plane-fixture-smoke-20260325_resume_fixture
```

结果：

- 状态：成功
- smoke 类型：fixture harness，不调用真实 relay websocket
- 已覆盖消息链路：
  - `pc_hello -> hello_ack`
  - `workspace_snapshot`
  - `command_dispatch -> command_ack(accepted)`
  - `command_ack(accepted) -> event(running) -> output_chunk(seq=1) -> reconnect hello -> output_resume_request(after_seq=1) -> replayed output_chunk(seq=2) -> result(done) -> artifact_manifest`
  - `command_dispatch -> command_ack(accepted_but_queued)`
- `artifact_manifest` 当前来自真实 `artifact_index.json + artifact_file_binding_index.json` 本地 truth-projection
  说明：当前 fixture 已覆盖双 artifact，其中一条使用最新 uploaded binding 的 `download_ref`，另一条保持 `download_ref = null`
- 已覆盖拒绝 / fencing：
  - `unsupported_backend`
  - `stale_connection_epoch`
- 清理检查：通过
  说明：这条 smoke 不拉起外部进程或监听端口，结果文件显式记录 `cleanup.required=false`

## 当前已确认结论

1. 当前仓库已经有可运行的最小 PC control-plane skeleton，不再只有文档。
2. `command_dispatch -> command_ack` 这条最小链路当前已能区分 `accepted` 与 `accepted_but_queued`。
3. canonical `event` 当前已能按 `event_id` 收口到命令时间线，并可携带 `effective_execution`。
4. canonical `output_chunk` 当前已能按 `stream_id + seq` 收口，并保留 `kind / text / delta / status` 最小流式载荷。
   当前 fixture 已覆盖单机 loopback 下的 reconnect -> `output_resume_request(after_seq=1)` -> selective replay；`tests/test_pc_control_plane_client.py` 还额外覆盖了真实 websocket 往返下的 server-driven resume。
5. canonical `result` 当前已能按 `result_id` 收口，并保证同一 `command_id` 只有一个 canonical result。
6. canonical `artifact_manifest` 当前已能从真实 `artifact_index.json + artifact_file_binding_index.json` 投影最小 artifact metadata，并与本地 artifact truth 保持分层。
7. `unsupported_backend` 当前会在 server runtime 侧被显式拒绝，而不是静默降级。
8. `connection_epoch` fencing 当前已经工作，旧连接的 heartbeat 会收到 `stale_connection_epoch`。

## 证据

- `_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_resume_fixture/smoke_result.json`
- `_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_resume_fixture/tasks/thread_cmd_001/runs/task_cmd_001/stream.events.jsonl`
- `_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_resume_fixture/tasks/thread_cmd_001/runs/task_cmd_001/artifacts/artifact_index.json`
- `_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_resume_fixture/tasks/thread_cmd_001/runs/task_cmd_001/artifacts/artifact_file_binding_index.json`

## 当前未覆盖项

以下内容还没有纳入这条 fixture smoke：

- 真实 websocket 往返
  说明：single-PC live `hello / workspace_snapshot / stale_connection_epoch` 已由 `docs/reference/pc_control_live_smoke_validation.md` 单独留证；本文件仍只记录 fixture harness 自身的覆盖面
- live `/v1/files` / COS external-delivery 的 artifact evidence
- 多 `PC` 路由与订阅

因此，这轮结论目前只覆盖：

- 单机本地 in-memory control-plane loopback
- 当前已实现的 `hello / snapshot / dispatch / ack / event / output_chunk / output_resume_request / result / artifact_manifest / fencing` 骨架
- 明确的 gap 记录，而不是完整 Phase 1 联调完成
