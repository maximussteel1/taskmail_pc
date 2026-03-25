# Project State

## Current Snapshot

- Updated At: 2026-03-26
- Current Runtime Stage: mail-first runtime active / post-Phase-8 direct compatibility line maintained
- Current Planning Mainline: `VPS-first multi-PC control plane`
- Status: Active
- Current Truth Layer: `docs/current/*`
- Bootstrap Entry: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config <mail_config.bot.local.yaml>`
- Hosted Loop Entry: `.\.venv\Scripts\python.exe -m mail_runner.host --config <mail_config.bot.local.yaml> --runtime-dir <_tmp_live_mail_runner>`
- Observability Entry: `.\.venv\Scripts\python.exe -m mail_runner.observe --config <_tmp_live_mail_runner\mail_config.loop_30s.yaml> status`
- Test Command: `.\.venv\Scripts\python.exe -m pytest`
- Latest Recorded Full-Suite Validation: `2026-03-24` -> `452 passed`
- Note: `2026-03-24` 已为 shared `/control` current-session session-action 扩展补跑 `.\.venv\Scripts\python.exe -m pytest`，结果 `452 passed`
- Note: `2026-03-24` 已在 `mail_config.bot.relay.local.yaml + _tmp_live_mail_runner` 这组 relay-enabled host 上，完成 Android 真机 `thread_023` fresh-session smoke；后续 current-session plain direct `reply` 与 current-session direct `/status` 两条样本都已收齐 Android retained send record、`session_action_closeout.json`、canonical mail 与 closeout bundle，且两条 bundle 的 `same_run_bind.effective_bind_level` 都稳定为 `request_id`
- Note: `2026-03-25` 已在 repo-side 把 `VPS-first` Phase 1 推进到 Slice H first-pass：`pc_hello / hello_ack`、`workspace_snapshot`、`execution_policy`、`command_dispatch -> command_ack -> event -> output_chunk -> result -> artifact_manifest`、`unsupported_backend` 显式拒绝、`connection_epoch` fencing、`event_id/result_id` 去重、`effective_execution` 回填
- Note: `2026-03-25` 已补跑定向回归 `.\.venv\Scripts\python.exe -m pytest tests/test_relay_server_pc_control_protocol.py tests/test_relay_server_pc_command_store.py tests/test_relay_server_pc_control_runtime.py tests/test_pc_control_plane_client.py tests/test_pc_control_plane_projection.py`，结果 `28 passed`
- Note: `2026-03-25` 已补跑 PC control-plane fixture smoke，并把 `artifact_manifest` 从 synthetic payload 切到真实 `artifact_index.json + artifact_file_binding_index.json` truth-projection；结果路径：`_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_truth_projection/smoke_result.json`
- Note: `2026-03-25` 已在 repo-side 补上 `output_resume_request` first-pass：server 侧可按已有 `stream_id + seq` cursor 下发 resume request，client 可按 `after_seq` 补发缺失尾段；当前这部分已由 protocol/runtime/client 定向测试覆盖
- Note: `2026-03-25` 已继续推进 Slice G closeout：`.\.venv\Scripts\python.exe -m pytest tests/test_pc_control_plane_client.py tests/test_relay_server_pc_control_runtime.py` 结果 `12 passed`，并已补跑 `.\.venv\Scripts\python.exe .\scripts\pc_control_plane_fixture_smoke.py --run-name pc-control-plane-fixture-smoke-20260325_resume_fixture`；当前 fixture 已覆盖 reconnect -> `output_resume_request(after_seq=1)` -> selective replay，本地结果路径：`_tmp_pc_control_plane_fixture_smoke/pc-control-plane-fixture-smoke-20260325_resume_fixture/smoke_result.json`
- Note: `2026-03-25` 已补上 `OpenCode SDK` 的同层 persisted stream evidence：repo-side 新增 `tests/test_opencode_sdk_adapter.py` 与 `tests/test_sdk_stream_smoke.py`，并补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_opencode_sdk_adapter.py tests/test_sdk_stream_smoke.py tests/test_observe.py tests/test_health_semantics.py tests/test_app_phase3.py`，结果 `50 passed`；真实 `sdk_stream_smoke` 也已在提权环境下成功留证，结果路径：`_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/stream_smoke_result.json`
- Note: `2026-03-25` 已继续推进 Slice H closeout：successful external delivery 现在会在 artifact 根目录下落 `external_delivery_index.json`，`artifact_manifest` 投影会优先复用这份 provider/url evidence；并已补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_external_delivery.py tests/test_pc_control_plane_projection.py tests/test_imports.py`，结果 `11 passed`。真实 `artifact_contract_smoke` 也已补到 live local relay `/v1/files` roundtrip，结果路径：`_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_file_surface_clean/smoke_result.json`。规划层当前应把 `/v1/files` 读成 artifact external-delivery owner lane，把 `COS` 读成 cutover 前临时兼容线
- Note: `2026-03-25` 已继续推进 `/v1/files` owner-lane cutover/hardening：repo-side 新增 `external_delivery_backend_preference=auto|cos|file_surface`，允许在 `COS` 仍保留配置时显式优先走 relay `/v1/files`；并补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_external_delivery.py tests/test_imports.py`，结果 `15 passed`。真实 `artifact_contract_smoke` 也已改为显式 `file_surface` preference，结果路径：`_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/smoke_result.json`
- Note: `2026-03-25` 已把 `artifact_contract_smoke` 扩成可重复的 live relay-host `/v1/files` 模式，并补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_artifact_contract_smoke.py tests/test_external_delivery.py tests/test_imports.py`，结果 `10 passed`。随后已在 `mail_config.bot.relay.local.yaml` 上完成真实 VPS relay `/v1/files` upload + metadata/content roundtrip，结果路径：`_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/smoke_result.json`
- Note: `2026-03-25` 已确认当前 live deployment 的历史 artifact matrix 里仍有 `5` 个 `>32 MiB` APK 样本；repo-side 因此把 cutover 语义收成“`/v1/files` owner lane + oversize 仍暂走 `COS` 兼容线”，并补上对应 external-delivery 路由测试
- Note: `2026-03-25` 已把 `mail_config.bot.relay.local.yaml` 切到 `external_delivery_backend_preference=file_surface`，并通过 `scripts/manage_mail_runner.ps1 restart -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup` 重启 live runner；当前 host pid=`34380`。这一步应读成“owner lane preference 已切到 `/v1/files`，但 `>32 MiB` 样本仍暂时保留 `COS` 兼容交付”
- Note: `2026-03-25` 已在 live deployment 下补到第一条真实 owner-lane 业务样本：`thread_110 / 20260325_210103_9eae` 产出 `22 MiB` `owner_lane_probe.bin`，terminal status mail、`artifact_file_binding_index.json` 与 `external_delivery_index.json` 三处都指向 `provider=file_surface`；汇总路径：`_tmp_live_mail_artifact_probe/artifact-probe-v2-20260325_210034-f82f17/summary.json`
- Note: `2026-03-25` 已在同一套 live cutover 配置下补到第一条真实 oversize 兼容样本：`thread_112 / 20260325_211000_7c92` 产出 `34 MiB` `owner_lane_probe_oversize.bin`，`external_delivery_index.json` 记录 `provider=cos`；汇总路径：`_tmp_live_mail_artifact_probe/artifact-probe-codex-oversize-20260325_210950-b9f355/summary.json`
- Note: `2026-03-25` 已新增 `scripts/external_delivery_window_report.py`，用于扫描 task-root 下最近的 `external_delivery_index.json` 并按 owner-lane 预期标出异常样本；首份 live 报告路径：`_tmp_live_mail_runner/external_delivery_window_report_20260325.json`，当前 `reported_runs=2`、`expectation_mismatch_count=0`
- Note: `2026-03-25` 已新增 `scripts/pc_control_live_smoke.py`，并在真实 public relay `/pc-control` 上补到 single-PC live bring-up 证据：unique probe `pc_id` 已跑通 `pc_hello -> hello_ack(connection_epoch=1) -> workspace_snapshot -> reconnect hello(connection_epoch=2) -> stale heartbeat -> stale_connection_epoch`；结果路径：`_tmp_pc_control_live_smoke/pc-control-live-smoke-20260325-single-pc-phase1-probe-unique/smoke_result.json`。这一步应读成“live websocket bring-up/fencing 已留证，但 live dispatch / multi-PC routing 仍未闭环”
- Note: `2026-03-25` repo-side 已补上 operator-only `POST /debug/pc-control/dispatch` 与 `scripts/pc_control_operator_dispatch.py`，并补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_pc_control_operator_dispatch.py tests/test_imports.py tests/test_relay_server_app.py tests/test_relay_server_runtime.py`，结果 `25 passed`。这一步应读成“repo-side 已有 live dispatch 注入路径，但真实 VPS relay 还需要部署这版代码后，才能补跑 live `command_dispatch -> command_ack -> event -> result`”
- Note: `2026-03-25` 已把包含 `POST /debug/pc-control/dispatch` 的 relay 代码真实部署到 VPS，并在 `pc-home / workspace_969e9b323b70` 上补到 single-PC live dispatch 闭环：第一次显式 `profile=default` 暴露出 `Codex SDK` adapter 把 `default` 错当成必须查映射的实现缺口；repo-side 同日已修复 `default profile -> unset profile` 语义，并补跑 `.\.venv\Scripts\python.exe -m pytest tests/test_codex_sdk_adapter.py tests/test_cli_adapters.py tests/test_pc_control_plane_client.py`，结果 `42 passed`。修复后重启 live host，再次以显式 `profile=default` 下注入真实 `new_task`，已真实收齐 `command_ack(accepted) -> event(accepted/running/done) -> result(done) -> output_chunk(seq=1..5)`；结果路径：`_tmp_pc_control_live_smoke/pc-control-live-smoke-20260325-live-dispatch-default-profile/smoke_result.json`
- Note: `2026-03-26` 已新增 `scripts/pc_control_live_roundtrip_smoke.py` 与对应 repo-side 纯函数回归（`.\.venv\Scripts\python.exe -m pytest tests/test_pc_control_live_roundtrip_smoke.py tests/test_pc_control_live_smoke.py tests/test_imports.py`，结果 `7 passed`），并在真实 public relay `/pc-control` 上补到 single-PC live roundtrip evidence：`command_dispatch -> command_ack -> event(accepted/running) -> output_chunk(seq=1) -> reconnect hello_ack(connection_epoch=2) -> output_resume_request(after_seq=1) -> selective replay(seq=2..3) -> event(done) -> result(done) -> artifact_manifest(download_ref_source=external_delivery_index.file_surface)`；结果路径：`_tmp_pc_control_live_smoke/pc-control-live-roundtrip-smoke-20260326-single-pc-replay-artifact-rerun2/smoke_result.json`
- Note: `2026-03-26` 已新增 `scripts/pc_control_live_multi_pc_smoke.py` 与对应 repo-side 纯函数回归（`.\.venv\Scripts\python.exe -m pytest tests/test_pc_control_live_multi_pc_smoke.py tests/test_pc_control_live_roundtrip_smoke.py tests/test_pc_control_live_smoke.py tests/test_imports.py`，结果 `11 passed`），并在真实 public relay `/pc-control` 上补到 multi-PC live routing evidence：双 probe `pc_id` 同时在线并各自上报独立 `workspace_id` 后，定向 dispatch A 只进入 probe A、dispatch B 只进入 probe B，且两条命令都在远端 `commands.json` 收成 `ack_status=accepted -> event(accepted/running/done) -> result(done)`；结果路径：`_tmp_pc_control_live_smoke/pc-control-live-multi-pc-smoke-20260326-routing-rerun1/smoke_result.json`

## Current Runtime Facts

- 当前系统仍是 mail-first：mail 是默认控制面、默认 receipt truth、默认 artifact/history truth
- PC 仍是 task execution truth；relay 不接管 task execution
- 仓库内已经存在独立的 `pc_control_plane_client`、`pc_control_protocol`、`pc_control_runtime` 与对应 store/test/fixture，用于推进 `VPS-first` 主线；但这套骨架当前还不替代 `docs/current/*` 所描述的 mail-first runtime truth
- 当前正式 direct surface 仅限：
  - direct `new_task`
  - bootstrap `[SYNC]` `v1` / `v2`
  - shared `/control` current-session direct `/status`
  - shared `/control` current-session plain direct `reply`
  - shared `/control` relay-side `transport_probe`
  - current-session direct `/status`
  - current-session plain direct `reply`
  - active-session detail read sidecar
  - relay `/v1/files` oversized-artifact file surface
- current-session direct `/status` 与 plain `reply` 当前仍是 bridge-to-mail，不是新的 direct terminal-result API
- `/control` 当前会返回三类 direct result frame：
  - bootstrap `[SYNC]` `v2` 的 `bootstrap_result`
  - current-session direct `/status` / plain `reply` 的 `session_action_result`
  - relay-side `transport_probe` 的 `transport_probe_result`
- 其中 `session_action_result` 当前只表示 `mail_ingress_submission` 与 `session_action_closeout` 锚点快照，不替代最终 canonical mail outcome
- relay `/v1/files` 当前用于超阈值 artifact 的 relay-hosted external delivery；本地 artifact truth 仍保持在 `RunArtifact` + `artifact_index.json`
- external delivery 当前支持 `external_delivery_backend_preference=auto|cos|file_surface`；默认 `auto` 保持兼容口径下的 COS-first 选择，而 `file_surface` 可在 `COS` 仍保留配置时显式优先走 relay `/v1/files`
- 成功的 oversized external delivery 当前还会在 `runs/<task_id>/artifacts/external_delivery_index.json` 下落 provider/url/expires_at 级 evidence；若走 relay `/v1/files`，`artifact_file_binding_index.json` 仍继续保留 transport-facing `artifact_id -> file_id` 绑定
- 每轮 run 当前会落 `runs/<task_id>/canonical_summary.json`
- current-session direct `/status` 与 plain `reply` 当前会落 `session_actions/<request_id>/session_action_closeout.json`
- `scripts/build_taskmail_closeout_bundle.py` 当前可组装 `taskmail_daily_closeout_bundle.json`，用于 closeout / parity / bind 证据汇总
- direct post-creation resolver 当前优先读 `session_state`，缺失时允许回退 `thread_state`，但 identity 冲突会明确 reject

## Current Documentation Reading Order

1. 当前行为与协议：`docs/current/*`
2. TaskMail direct relay/control/file 当前事实：
   - `docs/current/taskmail_direct_control_file_contract.md`
   - `docs/current/android_runner_communication_contract.md`
   - `docs/current/mail_protocol.md`
3. 当前发出内容与附件/外链显示：
   - `docs/current/pc_mail_output_protocol.md`
   - `docs/current/multimedia_mail_protocol.md`
4. 当前主线、后继候选线、冻结线、closeout/handoff 导航：
   - `docs/plans/README.md`
   - `docs/plans/coding_backlog.md`

## Planning Status

- 当前代码行为仍是 mail-first，但 future-direction mainline 已切到 `VPS-first 多 PC 控制面`
- 当前 repo-side 主线进度应读作：
  - Slice A-C 已落地最小协议、store、runtime、client、测试与 fixture skeleton
  - Slice D 已落地最小 `command_dispatch -> command_ack` 骨架与显式拒绝路径
  - Slice E-F 已落地 canonical `event / result`、`event_id/result_id` 去重、`effective_execution` 回填，以及最小 client/runtime/store/test/fixture 闭环
  - Slice G 已落地 first-pass canonical `output_chunk` packet、`stream_id + seq` 去重、基于已落盘 `stream.events.jsonl` 的 reconnect resend、显式 `output_resume_request` / server-driven resume、fixture loopback selective replay，以及 websocket roundtrip 回归
  - Slice H 已落地 first-pass canonical `artifact_manifest` packet、store/runtime/client/tests/fixture，以及基于真实 `artifact_index.json + artifact_file_binding_index.json` 的本地 truth-projection evidence；successful external delivery 现在还会下落 `external_delivery_index.json`，并已补上 live local relay `/v1/files` artifact evidence、真实 VPS relay `/v1/files` upload + metadata/content roundtrip evidence，以及 live deployment 下 `22 MiB -> file_surface` / `34 MiB -> cos` 的真实业务样本。planning 层当前应把 `/v1/files` 读成 owner lane，把 `COS` 读成 cutover 前兼容 lane；repo-side 现在也已支持通过 `external_delivery_backend_preference=file_surface` 在 `COS` 仍保留配置时显式切向 `/v1/files`
  - `OpenCode SDK` 已落地最小同层 `stream.events.jsonl`，当前 residual gap 已收窄为“true incremental streaming 尚未验证”，不再是“完全没有 same-layer stream evidence”
  - single-PC live `/pc-control` bring-up、live dispatch、live replay、以及 live `artifact_manifest` 当前都已留证：真实 public relay 已补到 `hello_ack / workspace_snapshot / stale_connection_epoch`、`command_dispatch -> command_ack -> event -> result -> output_chunk`、`output_resume_request(after_seq=1)` selective replay，以及 `artifact_manifest(download_ref_source=external_delivery_index.file_surface)`
  - multi-PC live routing 当前也已留证：双 probe `pc_id` 同时在线时，定向 dispatch 已可稳定只命中目标连接，不再串投到另一条 websocket；此前合写的“多 `PC` 路由/订阅”现在应拆开读，routing 已完成，observer / subscription 侧证据尚未单列
  - 当前 live deployment 所需的首批 `COS` 兼容样本也已留证，不再是默认下一刀
  - 规划层当前应把 mail 线读成 cutover 前兼容层，而不是长期 fallback / 双主线；近期目标是直接切入 `VPS-first`
  - 规划层当前也应把 `COS` 读成 cutover 前兼容 external-delivery 线，而不是长期双通道
- 旧的 `TaskMail direct relay/control/file` 相关文档现在应按 compatibility / closeout / migration reference 读法维护
- `2026-03-24` 的 `thread_023` 真机 smoke 与其他 direct closeout 证据仍然有效，但它们不再定义未来主线，只定义当前兼容行为与历史迁移材料
- `VPS ingress truth v1` 当前不再单独读成“主线之后的候选线”，而应作为新主线的前置参考
- `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md` 当前仍主要承担“目标切片 + 验收门”职责；判断现实落地进度时，需要同时看 `docs/reference/pc_control_plane_fixture_smoke_validation.md`、`docs/reference/sdk_stream_smoke_validation.md` 与 `docs/reference/artifact_contract_smoke_validation.md`
- `P9 HTML` 仍是冻结线，不会因为这次 authority reset 自动重新变成当前主线

## Next Candidate Lines

- 先继续收硬 `/v1/files` owner lane 的 cutover/decommission 观察窗口与运行门槛，不再默认继续补 artifact lane 证据
  - 具体执行清单见 `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`
- 当前 artifact lane 已至少有一条真实 `file_surface` 样本与一条真实 `COS` oversize 兼容样本；接下来更需要确认的是观察窗口内 provider 是否持续符合“普通样本走 `/v1/files`、`>32 MiB` 样本才走 `COS`”的口径
- 如果 `pc-control` 还要继续扩 live 联调，优先把更高层多 `PC` observer / subscription 侧需求单列，而不是重复补 single-PC roundtrip 或多 `PC` routing
- 同步准备 cutover/decommission 口径：后续 planning 不再把 mail 设计成长期 fallback 常驻线
- 同步准备 `/v1/files` cutover / `COS` decommission 口径：后续 planning 不再把 `COS` 设计成长期 external-delivery 常驻线
- 需要交接给下一位实现者时，直接按 `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md` 中的 `6.1 COS 退场口径` 判断：先看 `/v1/files` 是否已满足 owner-lane 条件，再决定 `COS` 只保兼容还是进入删除准备
- 继续保持本地 artifact truth 为 `RunArtifact + artifact_index.json`，不要把 control-plane packet 反向当成 truth layer
- 继续维持 `docs/current/*` 所描述的 mail-first 兼容行为稳定，不把当前 direct closeout 线误当成新主线 owner queue
- 如需重启 HTML / P9，只能在新主线明确排期并显式 reopen 后进行，不能隐式借用旧 backlog 口径

## Historical Note

- 更早的 Phase 0-8 里程碑、relay bootstrap 演进、以及已明确结束的 closeout 记录，当前主要保留在 `git log` 与 `docs/plans/*` 对应 handoff/evidence 文档中
