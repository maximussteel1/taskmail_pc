# Phase 2 Direct-Outbound Closeout Handoff

## Status

- Date: 2026-03-21
- Scope: repository-side handoff from Phase 2 direct-outbound closeout into Phase 3 direct inbound update bridge
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/phase2_direct_outbound_contract_v1.md`
  - `docs/plans/phase3_direct_inbound_mapping_v1.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/plans/vps_relay_deploy_runbook.md`

## Purpose

把 Phase 2 的“是否准备好”切换成“已经闭环，下一阶段该做什么”。

这份 handoff 不重新定义 Phase 2 合同，也不改变当前 Layer 1 事实；它只负责把下一次会话的起点写清楚。

## Phase 2 Closeout Snapshot

当前仓库侧可以把 Phase 2 v1 读作已经闭环的第一刀：

- live relay 已接受 direct `new_task`，并桥回当前 bot-mailbox 首封入口
- accepted direct path 已做过 live 验证
- 临时 negative-path hook 已完成 live 验证：
  - ack-level fallback-classified rejection
  - ack-level hard rejection（带 `packet_ack.error_code`）
- 真机 smoke 已把上述三条分支全部走通
- 当前 direct outbound scope 仍然只包含 `new_task`
- reply、control action 和 read-side update 仍然不属于 Phase 2 范围

当前不应把这次 closeout 误读成：

- Android 已经 direct-first
- mail read-side 已经被替换
- direct reply/control contract 已经冻结

## Handoff Decisions

1. 仓库侧 Phase 2 direct-outbound v1 现在可以视为关闭。
2. 下一活跃阶段是 Phase 3 direct inbound update bridge，而不是继续扩 Phase 2 outbound action 集。
3. Phase 2 的临时 negative-path hook 已完成验证并已关闭；它不应被吸收到正式业务合同里。
4. Phase 3 必须复用现有 TaskMail 业务语义，不要发明第二套 session / state 模型。
5. Phase 3 的仓库侧第一份 read-side mapping 现在已经显式落在 `docs/plans/phase3_direct_inbound_mapping_v1.md`。

## Phase 3 Starting Boundary

### PC / VPS 侧第一刀

- 冻结一个 `phase3-direct-inbound-mapping-v1`
- 先只覆盖：
  - workspace summary
  - session detail timeline
  - question state
  - paused / running
  - done / failed
- 第一刀优先做“当前活跃 session 的 snapshot + delta”，不要先做全量 history API
- mail 输出继续保留，作为 fallback 和对账 truth 之一
- 不要把 direct reply/control、附件同步、全量历史读取塞进同一阶段首刀

### Android 侧第一刀

- 把 direct inbound update 映射进现有 local repository / UI seam
- 保持 mail-derived 与 direct-derived 状态可共存
- 先优先覆盖 session detail 读侧，而不是先做大而全的 workspace 浏览
- reply/control 继续保持 mail-first，直到后续阶段显式扩 scope

### Shared Artifact Package

Phase 3 起步至少要留下三份共享工件：

- `phase3-direct-inbound-mapping-v1`
- representative fixture set
- coexistence note for mail-derived versus direct-derived state

## Recommended First Slice

建议把下一刀严格收窄为：

1. PC / VPS 只发“当前活跃 session”的 direct snapshot + delta
2. Android 先只在 session detail 页面消费它
3. 先跑通：
   - `RUNNING`
   - `QUESTION`
   - `PAUSED`
   - `DONE`
   - `FAILED`
4. workspace summary 放到第二刀
5. direct reply/control、附件、history API 放到后续阶段

## Current Conclusion

仓库侧 Phase 2 direct-outbound v1 handoff 现在已经明确。

下一次会话不需要再讨论 Phase 2 是否准备好；默认起点就是 Phase 3 direct inbound update bridge。

如果后续需要重新扩 direct outbound scope，应另写新的 scope note，而不是继续隐式追加到 Phase 2 v1。
