# VPS-First 多 PC 控制面主线说明（v0.1）

## Status

- Date: 2026-03-25
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

## First-Stage Scope

第一阶段应优先冻结并推进这些事项：

1. `PC` 作为节点注册到 `VPS`
2. `workspace_snapshot` 作为可路由资源清单
3. `backend / profile / permission / backend_transport` 的执行策略骨架
4. `command_dispatch -> command_ack -> event -> output_chunk -> result`
5. `output_chunk` 成为正式流式输出对象
6. `artifact_manifest` 与 `result` 从执行节点回推到 `VPS`

## Implementation Reading

当任务从“讨论主线”切到“开始编码 Phase 1”时，repo-side 推荐继续读：

- `docs/plans/vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `docs/plans/vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`

这两份文档的职责分别是：

- 实现设计：明确 Slice A-C 应落在哪些模块、复用哪些代码资产、不要误复用哪些旧 seam
- 验证矩阵：明确 Slice A-C 在 protocol/store/runtime/smoke 四层各自要拿到什么证据

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
5. 最后再逐步把 mail 从主控制面降级为 backup/export/notification。
