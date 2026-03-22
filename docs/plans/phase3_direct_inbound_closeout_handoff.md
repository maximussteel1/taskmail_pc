# Phase 3 Direct-Inbound Closeout Handoff

## Status

- Date: 2026-03-22
- Scope: repository-side handoff from Phase 3 direct inbound update bridge closeout into Phase 4 dual-stack parity and primary-path switch
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/phase3_direct_inbound_mapping_v1.md`
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-next-session-handoff-2026-03-22-phase3-workspace-refresh-closeout.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-next-session-handoff-2026-03-22-phase4-dual-stack-start.md`

## Purpose

把 Phase 3 的“第一刀是不是还要继续扩 scope”切换成“仓库侧 closeout 已经足够，下一阶段该比较什么”。

这份 handoff 不重写 Phase 3 合同，也不改变当前 Layer 1 事实；它只负责把 Phase 3 冻结边界和
Phase 4 起点写清楚。

## Phase 3 Closeout Snapshot

当前仓库侧可以把 Phase 3 v1 读作已经闭环的第一刀：

- `phase3_direct_inbound_mapping_v1.md`、`phase3_direct_inbound_wire_v1.md`、
  `phase3_direct_inbound_fixture_package_v1.md` 三份共享 freeze artifact 已齐备
- 仓库内已有对应的 parser / emitter / fixture loader / thread-store-backed provider
- representative fixtures 已落盘，并有回归覆盖
- Layer 1 文档已经把这条 Phase 3 direct active-session-detail sidecar 固定为“可选、读侧、mail
  fallback 仍保留”的窄范围能力
- Android 侧在 2026-03-22 已新增 Phase 3 closeout 与 Phase 4 start handoff，当前口径已切到
  “先冻结 Phase 3，再进入 Phase 4 dual-stack parity”
- 当前用户可见的 status / result / reply-control canonical path 仍然是 mail；Phase 3 没有改变这一点

当前不应把这次 closeout 误读成：

- Android / PC 已经对所有 covered flows direct-first
- direct `reply` / direct `/status` contract 已经冻结
- mail read-side 或 fallback 已经可以删除
- Phase 3 应继续吸收 workspace summary 主路径替换、attachment sync 或 history API

## Handoff Decisions

1. 仓库侧 Phase 3 direct inbound v1 现在可以视为关闭的第一刀。
2. 下一活跃跨仓库阶段是 Phase 4 dual-stack parity and primary-path switch，而不是继续扩写 Phase 3。
3. Phase 3 剩余尾项若不改变 covered-flow 定义、rollback 规则或 Layer 1 合同，不再作为当前阶段 blocker。
4. 若要把 `reply`、`/status` 或更广的 direct read-side 纳入 covered flows，必须先单独冻结跨仓库
   contract，不能继续隐式追加到 Phase 3 v1。
5. mail fallback 继续保留，并继续作为 parity 对账与 rollback 可信度的一部分。

## Phase 4 Starting Boundary

### Covered Flows

建议先把 Phase 4 的 covered flows 收窄读作：

- direct `new_task`
- direct accepted / mail fallback / hard rejection 三类既有 send-side outcome
- 与这些 flow 相关的 user-visible thread outcome / summary semantics 对账

当前不建议默认把以下内容并入 Phase 4 covered flows：

- direct `reply`
- direct `/status`
- 全量 workspace / history read path 切换
- 移除 mail fallback

### PC / VPS 侧第一批工件

Phase 4 起步至少要留下三份共享工件：

- parity checklist
- mismatch ledger
- rollback trigger note

仓库侧建议先显式对齐：

- direct accepted vs mail fallback vs hard rejection 的用户可见语义
- `packet_ack` / mail delivery / thread outcome 的对账口径
- covered flow 下哪些差异只记日志，哪些差异触发回退或停流

### Android 侧配套前提

- Android 侧继续保持 mail fallback 可用
- 仅对 parity 足够的 flow 考虑 primary-path switch
- reply / control 仍需等待单独的 contract freeze

## Current Conclusion

仓库侧 Phase 3 closeout 现在已经可以显式写出。

下一次会话不需要再讨论 Phase 3 第一刀是否成立；默认起点应转为 Phase 4 dual-stack parity /
mismatch / rollback 设计。

如果后续要继续扩 direct `reply`、direct `/status` 或更广读侧范围，应另写新的 contract /
scope note，而不是继续把这些内容塞回 Phase 3 v1。
