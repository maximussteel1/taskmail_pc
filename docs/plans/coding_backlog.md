# Next Development Plan

## Status

- Date: 2026-03-25
- Scope: repository-side active mainline note after the 2026-03-25 authority reset
- Source of truth: `docs/current/*`, `docs/plans/README.md`, `state.md`

## Current Reading

- 当前代码行为仍是 mail-first
- 当前 future-direction active mainline 已切到 `VPS-first 多 PC 控制面`
- 旧的 `TaskMail direct relay/control/file` 不再是未来主线，而是 compatibility / closeout / migration reference
- `P9 HTML` 与 broader outbound convergence 不是当前默认编码队列

## Current Mainline

当前主线第一阶段应优先围绕以下能力展开：

1. `PC` 节点注册与鉴权
2. `heartbeat` 与 `last_seen` 投影
3. `workspace_snapshot`
4. `backend / profile / permission / backend_transport` 执行策略骨架
5. `command_dispatch -> command_ack`
6. `event`
7. `output_chunk`
8. `result`
9. `artifact_manifest`

这些内容应统一按两层阅读：

- 未来主线边界：看 `docs/plans/android_pc_vps_evolution_authority.md`
- 当前主线 owner note：看 `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
- 当前阶段实施顺序：看 `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md`

## Still Open On This Mainline

当前至少仍保留这些开放项：

1. `PC <-> VPS` 字段级 schema 还未冻结
2. `workspace` 的持久化主键与 snapshot/upsert 规则还未冻结
3. `output_chunk` 的 replay 与断线恢复规则还未冻结
4. `/v1/files` owner lane 的 cutover / `COS` decommission 还需要按独立 checklist 收口
5. 旧 direct relay/control/file 资料还没有被彻底按 compatibility / closeout 读法重新整理

其中第 4 项当前应优先参考：

- `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`

## Reference Precursors

以下文档当前更适合作为新主线的前置参考，而不是单独的“下一条线”：

- `docs/plans/vps_ingress_truth_v1_checklist.md`
- `docs/plans/vps_ingress_truth_v1_execution_order.md`

## Not Current Queue

以下内容当前明确不应读作 active implementation queue：

- `p9_html_mail_projection_plan.md`
- `android_consumer_contract_alignment_plan.md`
- `android_consumer_protocol_freeze_note.md`
- `outbound_mail_contract_convergence_plan.md`
- `phase5_*` 文档集

## Guardrails

后续开发继续遵守以下边界：

1. 当前行为 truth 永远以 `docs/current/*` 为准
2. 不要把旧 direct line 再写成当前 future-direction mainline
3. mail 当前仍是实现侧默认控制面与结果面，但这不再等于未来主线
4. PC 仍是 task execution truth
5. 不把 `workspace` 规划成跨 PC 共享执行对象
