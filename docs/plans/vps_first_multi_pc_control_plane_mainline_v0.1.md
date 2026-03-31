# VPS-First 多 PC 控制面主线说明（v0.1）

## Status

- Date: 2026-03-27
- Scope: repository-side active mainline owner note after the 2026-03-25 authority reset
- Source of truth:
  - `docs/current/*`
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-multi-pc-control-plane-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-legacy-mail-to-control-plane-mapping-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-command-event-payload-appendix-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-result-artifact-errorcode-appendix-v0.1.md`

## Current Reading

当前 repository-side future-direction mainline 应这样读：

- 目标不是继续把 mail-first 现状外面再包更多 direct 例外
- 目标是建立一个 `VPS-first` 统一控制面
- 一个平台同时管理多台 `PC`
- `workspace` 明确属于某台 `PC`
- `session` 一旦创建就固定绑定到 `workspace + pc`
- `backend/profile/permission` 必须作为控制面一等字段进入主线，而不是继续散落在旧 mail 语义里
- 控制面全量字段形状现在已经足够冻结成 Android 与 PC/VPS 的共享开发 baseline
- 近期开发目标应按“直接切入 `VPS` 主控制面”读取，而不是继续设计长期 mail 共存
- mail 只作为 cutover 前兼容/迁移层保留，不是长期 backup/fallback 常驻架构
- artifact external delivery 的 owner lane 应收敛到 `VPS /v1/files`；`COS` 目前只可按 cutover 前兼容线读取，不应再被读成长期并存的第二 owner lane
- mail control-plane 的 repo-side 收口应优先通过单一 `control_plane_mode` 总开关推进，而不是继续扩散多个局部布尔位；推荐路径是 `hybrid -> vps_only`
- 当前 `2026-03-27` 的 repo-side checkpoint 已应按 `vps_only` cutover 读：PC host 不再 consume bot mailbox 作为控制面入口，VPS relay 的旧 mail-bridge surface 也不再应读成 active lane
- 在这个 checkpoint 上，`pc-control`、`/v1/files`、bootstrap `[SYNC] v2` 与 Android-facing `POST /v1/android/create-session` 仍应读成 active seam；服务可在验证窗口之间被主动保持离线，不必为了“持续在线”回退到旧 mail lane
- 当前 Android 主链的 relay-visible `shared task_root` sync 只能读成 migration scaffold，不再应继续投资为长期 owner seam
- 当前下一条 owner queue 应切到：用 relay-native projection store 替代 Android 读面对 `shared task_root` 镜像的在线依赖，且首轮不改 Android contract

## First-Stage Scope

第一阶段应优先冻结并推进这些事项：

1. `PC` 作为节点注册到 `VPS`
2. `workspace_snapshot` 作为可路由资源清单
3. `backend / profile / permission / backend_transport` 的执行策略骨架
4. `command_dispatch -> command_ack -> event -> output_chunk -> result`
5. `output_chunk` 成为正式流式输出对象
6. `artifact_manifest` 与 `result` 从执行节点回推到 `VPS`

## Current Owner Focus

截至 `2026-03-29`，当前主线下一个应优先推进的 owner focus 是：

- 停止把 `sync_relay_task_root.py` 优化成更复杂的长期方案
- 把 Android `sessions / session-snapshot / session-history / session-updates` 的热路径从 relay-visible `task_root` 镜像切到 relay-native projection store
- 保持 Android app-facing contract 不变，避免把这次收敛错误扩成一次 Android 联动改版
- 保持 `PC` 本地 `task_root` 作为 execution / evidence truth，而不是把它迁成 `VPS` 主库
- 继续复用现有 `pc-control`、`workspace_snapshot`、`command/result`、`artifact_manifest` 与 `/v1/files` seam，而不是重新引入第二条同步通道

对应 owner 文档：

- `docs/plans/vps_relay_projection_store_mainline_v0.1.md`

## Implementation Reading

当任务从“讨论主线”切到“开始编码 Phase 1”时，repo-side 推荐继续读：

- `docs/plans/vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `docs/plans/vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`
- `docs/plans/vps_relay_projection_store_mainline_v0.1.md`
- `docs/plans/vps_relay_projection_store_schema_v0.1.md`
- `docs/plans/vps_relay_projection_publisher_protocol_v0.1.md`
- `docs/plans/vps_relay_projection_cutover_shadow_compare_v0.1.md`

这些文档的职责应这样读：

- `vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
  - 明确 Slice A-C 应落在哪些模块、复用哪些代码资产、不要误复用哪些旧 seam
- `vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`
  - 明确 Slice A-C 在 protocol/store/runtime/smoke 四层各自要拿到什么证据
- `vps_relay_projection_store_mainline_v0.1.md`
  - 冻结 Android live read path 收敛到 projection store 的 owner 方向与边界
- `vps_relay_projection_store_schema_v0.1.md`
  - 冻结 relay-side durable schema 与 Android 读面映射
- `vps_relay_projection_publisher_protocol_v0.1.md`
  - 冻结 PC publisher wire 规则、版本语义与 trigger matrix
- `vps_relay_projection_cutover_shadow_compare_v0.1.md`
  - 冻结双写、对账、切读源与 companion 退场门槛

## What This Mainline Is Not

这条主线当前明确不是：

- 共享 workspace 的多 PC 执行
- 运行中 session 的跨 PC 热迁移
- 把 repo/worktree/native session 整体搬到 VPS
- 继续把 `TaskMail direct relay/control/file` 当成长线 owner queue

## Relationship To Older Direct Work

旧的 direct relay/control/file 相关切片应继续按两层阅读：

- 作为 current behavior：看 `docs/current/*`
- 作为 compatibility / closeout / migration reference：看旧的 `docs/plans/*`

但它们不再是仓库当前 future-direction 的 owner line。

## Recommended Sequence

1. 先冻结 authority 与领域模型。
2. 再冻结 `PC <-> VPS` 最小协议。
3. 然后按 `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md` 落地 node registration 与 workspace inventory。
4. 再落地 command/event/result 骨架。
5. 再把 Android 读面从 relay-visible `shared task_root` 镜像切到 relay-native projection store。
   具体执行口径见 `docs/plans/vps_relay_projection_store_mainline_v0.1.md`
6. 把 artifact external-delivery 的 owner lane 收敛到 `VPS /v1/files`，并把 `COS` 保持在 cutover 前兼容范围内。
   具体执行口径见 `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`
7. 在 `VPS-first` cutover 条件满足后，按清单退场 mail control-plane / fallback 线；是否保留纯导出或归档能力，必须另开文档单独论证。
8. 在 `VPS /v1/files` cutover 条件满足后，按清单退场 `COS` external-delivery 线，而不是长期保留双通道。

## Current Checkpoint

截至 `2026-03-27`，当前主线更准确的 repo-side 读法是：

- `control_plane_mode=vps_only` 已不再只是 planning target，而是已经落到本机 live host config 与 VPS relay deploy env 的 current checkpoint
- relay `/healthz` 在该模式下应明确回 `taskmail_direct_ingress_enabled=false`，避免继续把旧 mail-bridge 凭据误读成 active ingress lane
- 旧 direct `new_task` 在该模式下应稳定返回 `unsupported_action`
- `pc-control` read-side 与 relay `/v1/files` 仍是恢复服务时首先要验证的两条 owner seam
- relay-visible `shared task_root` sync 当前仍是 live behavior 的 migration 依赖，但它不再应被读成未来主线的长期 owner seam
- `2026-03-27` 的真实 rerun 已确认上述 checkpoint 当前成立：VPS relay 恢复后，`vps_only_checkpoint_validation` 已重新通过 `healthz`、`pc-control`、live `/v1/files` roundtrip 与 direct `unsupported_action` 四项检查；同日 live observation window 也已明确给出 `window_ready=true`，但 `cos_decommission_candidate=false`
- 同日 repo-side 也已补齐 `/v1/files` transport-token consumer smoke：当前 `download_ref_source=external_delivery_index.file_surface` 的 `GET download_ref` 在携带 transport token 时返回 `200`，缺 token / 错 token 返回 `401 unauthorized`；这一步应读成“authenticated consumer seam 已成立”，不是“`/v1/files` 已升级成匿名公开下载面”
- 如果当前阶段不要求服务持续在线，则推荐先把文档、runbook、checklist 与 deploy 口径收硬，再在下一次集中 bring-up 时一次性做 re-enable validation
