# PC Control Live Smoke 验证结果

## 目的

记录单 PC live `/pc-control` 真实联调当前已经补到什么、证据在哪、还差什么。

## 本次记录对应环境

- 日期：`2026-03-26`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- relay host：`ws://124.223.41.153:8787/pc-control`
- health 入口：`http://124.223.41.153:8787/healthz`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\pc_control_live_smoke.py --config .\mail_config.bot.relay.local.yaml`
- roundtrip 入口：`.\.venv\Scripts\python.exe .\scripts\pc_control_live_roundtrip_smoke.py --config .\mail_config.bot.relay.local.yaml`
- multi-PC 入口：`.\.venv\Scripts\python.exe .\scripts\pc_control_live_multi_pc_smoke.py --config .\mail_config.bot.relay.local.yaml`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_smoke.py --config .\mail_config.bot.relay.local.yaml --run-name pc-control-live-smoke-20260325-single-pc-phase1-probe-unique
```

结果：

- 状态：成功
- smoke 类型：真实 public relay websocket + public `/healthz`
- probe `pc_id`：`pc-home-live-smoke-probe-unique`
- 已覆盖链路：
  - `pc_hello -> hello_ack(connection_epoch=1)`
  - `workspace_snapshot`
  - reconnect `pc_hello -> hello_ack(connection_epoch=2)`
  - stale heartbeat -> `error(code=stale_connection_epoch)`

同日继续补跑：

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_operator_dispatch.py `
  --config .\mail_config.bot.relay.local.yaml `
  --pc-id pc-home `
  --workspace-id workspace_969e9b323b70 `
  --command-type new_task `
  --session-id live_pc_control_default_profile_20260325_234755 `
  --command-id cmd_live_pc_control_default_profile_20260325_234755 `
  --execution-policy-json "{\"backend\":\"codex\",\"profile\":\"default\",\"permission\":\"default\",\"backend_transport\":\"sdk\"}" `
  --payload-json "{\"task_text\":\"Read-only live pc-control probe with explicit default profile. Do not modify repository files. Do not run tests. Reply with exactly one line: LIVE_PC_CONTROL_DEFAULT_PROFILE_OK\",\"timeout_minutes\":10,\"mode\":\"analysis_only\"}"
```

补充结果：

- 状态：成功
- live dispatch 注入面：真实 VPS relay `POST /debug/pc-control/dispatch`
- target `pc_id`：`pc-home`
- target `workspace_id`：`workspace_969e9b323b70`
- 已覆盖链路：
  - `command_dispatch`
  - `command_ack(accepted)`
  - `event(accepted -> running -> done)`
  - `result(final_status=done)`
  - `output_chunk(seq=1..5)`

在真正收口这条成功样本之前，同日还捕获到一个明确实现缺口：

- 首次 live dispatch 使用 `execution_policy.profile=default`
- 真实链路能到 `accepted -> running -> failed`
- 失败原因不是 relay/runtime，而是本地 `Codex SDK` adapter 把显式 `default` 错当成“必须查 profile 映射”
- 具体报错：`ValueError: Codex profile mapping is missing for profile 'default'`
- repo-side 已在同日修复为：adapter 层把 `profile=default` 视为“未指定 profile”，并补了本地回归
- 修复后重启 live host，再次使用显式 `profile=default` 的同类 dispatch，真实链路已成功收口

次日继续补跑：

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_roundtrip_smoke.py `
  --config .\mail_config.bot.relay.local.yaml `
  --run-name pc-control-live-roundtrip-smoke-20260326-single-pc-replay-artifact-rerun2
```

补充结果：

- 状态：成功
- smoke 类型：真实 public relay websocket + live operator dispatch + 远端 `commands.json` 直读
- probe `pc_id`：`pc-home-live-roundtrip-ifact-rerun2`
- target `workspace_id`：`workspace_969e9b323b70`
- 已覆盖链路：
  - `command_dispatch`
  - `command_ack(accepted)`
  - `event(accepted -> running)`
  - `output_chunk(seq=1)`
  - reconnect `pc_hello -> hello_ack(connection_epoch=2)`
  - `output_resume_request(after_seq=1)`
  - selective replay `output_chunk(seq=2..3)`
  - `event(done)`
  - `result(final_status=done)`
  - `artifact_manifest(download_ref_source=external_delivery_index.file_surface)`

同日继续补跑：

```powershell
.\.venv\Scripts\python.exe .\scripts\pc_control_live_multi_pc_smoke.py `
  --config .\mail_config.bot.relay.local.yaml `
  --run-name pc-control-live-multi-pc-smoke-20260326-routing-rerun1
```

补充结果：

- 状态：成功
- smoke 类型：真实 public relay websocket + 双 probe `pc_id` 并发在线 + live operator dispatch + 远端 `workspaces.json/commands.json` 直读
- probe `pc_id`：
  - `pc-home-live-multi-a-6-routing-rerun1`
  - `pc-home-live-multi-b-6-routing-rerun1`
- target `workspace_id`：
  - `workspace_live_multi_a_6-routing-rerun1`
  - `workspace_live_multi_b_6-routing-rerun1`
- 已覆盖链路：
  - 双 probe `pc_hello -> hello_ack`
  - 双 probe `workspace_snapshot`
  - 定向 dispatch A 只下发到 probe A，不串投到 probe B
  - 定向 dispatch B 只下发到 probe B，不串投到 probe A
  - 两条 dispatch 都在远端 `commands.json` 收成 `ack_status=accepted`、`event(accepted/running/done)`、`result(final_status=done)`

## 当前已确认结论

1. 当前 live VPS relay 的 `/pc-control` 已经可以接受真实 `pc_hello`，不是只在 fixture / local runtime 里存在。
2. `workspace_snapshot` 已经能进入 live relay 的 operator-facing health 视图。
   这次 probe 前后：
   - `node_count: 2 -> 3`
   - `workspace_count: 46 -> 69`
   - 增量正好是 `+1 node / +23 workspaces`
3. live reconnect fencing 当前已经工作。
   同一 probe `pc_id` 第二次 `hello` 后，旧 epoch heartbeat 被明确拒绝为 `stale_connection_epoch`。
4. 这层证据已经把“真实 websocket 往返”从纯 fixture gap 推进到“single-PC live bring-up 已经稳定可复现”。
5. 真实 VPS relay 上的 operator-only dispatch 注入面已经完成 live 闭环，不再只是 repo 内 app/runtime 回归。
   这次已真实收齐：
   - `command_ack(accepted)`
   - `event(accepted/running/done)`
   - `result(final_status=done)`
   - `output_chunk`
6. `execution_policy.profile=default` 当前语义已经在真实链路里重新验证：
   - 修复前：会在本地 adapter bootstrap 处失败
   - 修复后：显式 `profile=default` 已能稳定走到 `done`
7. single-PC live replay 当前已经不再只是 fixture 结论。
   真实 public relay 已在远端 `commands.json` 留下：
   - 首段 `output_chunk(seq=1)` 先入库
   - reconnect 后真实下发 `output_resume_request(after_seq=1)`
   - probe 只补发缺失尾段 `seq=2..3`
8. live `artifact_manifest` 当前也已经有 store-level evidence。
   这次真实入库的 manifest 明确记录：
   - `artifact_id = artifact-live-probe-report`
   - `download_ref_source = external_delivery_index.file_surface`
   - `artifacts_root = runs/task_live_pc_control_roundtrip_c-replay-artifact-rerun2/artifacts`
9. multi-PC live routing 当前也已经有真实 relay 证据。
   这次双 probe 同时在线时：
   - dispatch A 只进入 `pc-home-live-multi-a-6-routing-rerun1`
   - dispatch B 只进入 `pc-home-live-multi-b-6-routing-rerun1`
   - 两条命令在远端 `commands.json` 都保留了正确的 `pc_id / workspace_id`
10. 因此当前 `pc-control` live 证据不再只停在 single-PC。
    如果把此前合写的“多 `PC` 路由/订阅”拆开读，那么 routing 已经留证，剩下未单列 live 验证的是更高层 observer / subscription 侧证据。

## 证据

- `_tmp_pc_control_live_smoke/pc-control-live-smoke-20260325-single-pc-phase1-probe-unique/smoke_result.json`
- `_tmp_pc_control_live_smoke/pc-control-live-smoke-20260325-live-dispatch-default-profile/smoke_result.json`
- `_tmp_pc_control_live_smoke/pc-control-live-roundtrip-smoke-20260326-single-pc-replay-artifact-rerun2/smoke_result.json`
- `_tmp_pc_control_live_smoke/pc-control-live-multi-pc-smoke-20260326-routing-rerun1/smoke_result.json`
- `_tmp_live_mail_runner/tasks/thread_live_pc_control_default_profile_20260325_234755/runs/task_cmd_live_pc_control_default_profile_20260325_234755/result.json`
- `_tmp_live_mail_runner/tasks/thread_live_pc_control_default_profile_20260325_234755/runs/task_cmd_live_pc_control_default_profile_20260325_234755/summary.md`

关键字段：

- `hello_ack_one.connection_epoch = 1`
- `snapshot_observation.health_node_delta = 1`
- `snapshot_observation.health_workspace_delta = 23`
- `hello_ack_two.connection_epoch = 2`
- `stale_epoch_observation.code = stale_connection_epoch`
- `post_fix_success_with_explicit_default_profile.command_id = cmd_live_pc_control_default_profile_20260325_234755`
- `post_fix_success_with_explicit_default_profile.final_status = done`
- `post_fix_success_with_explicit_default_profile.event_types = [accepted, running, done]`
- `post_fix_success_with_explicit_default_profile.output_chunk_count = 5`
- `output_resume_request.payload.after_seq = 1`
- `observation.output_chunk_seqs = [1, 2, 3]`
- `observation.artifact_ids = [artifact-live-probe-report]`
- `observation.artifact_download_ref_sources = [external_delivery_index.file_surface]`
- `workspace_observation.success = true`
- `route_observation.probe_a.success = true`
- `route_observation.probe_b.success = true`
- `route_observation.probe_a.cross_message_present = false`
- `route_observation.probe_b.cross_message_present = false`

## 当前未覆盖项

- 更高层多 `PC` observer / subscription 侧证据

## 过程记录

- 这轮 single-PC bring-up 最初仍主要依赖 `/healthz.pc_control` 增量观察。
- 但在后续 live dispatch 取证阶段，当前这台 Windows 开发机已能稳定用 `work_bot.pem` 直接读取远端：
  - `/opt/mail_runner_relay/shared/state/pc_control/pc_nodes.json`
  - `/opt/mail_runner_relay/shared/state/pc_control/workspaces.json`
  - `/opt/mail_runner_relay/shared/state/pc_control/commands.json`
- 因此当前 live 取证已经不再局限于 `/healthz` 计数，而是可以直接引用远端 `pc_control` state 文件。

这意味着 single-PC live bring-up、live dispatch、live replay、live `artifact_manifest`，以及 multi-PC live routing 当前都已经有了真正的 store-level evidence。下一阶段如果还继续扩 `pc-control` live 联调，更应该把 observer / subscription 侧需求单列出来，而不是重复证明 single-PC 或定向 routing 基本可用。
