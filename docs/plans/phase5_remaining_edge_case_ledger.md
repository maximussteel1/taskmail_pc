# Phase 5 Remaining Edge-Case Ledger

## Status

- Date: 2026-03-22
- Scope: repository-side first draft baseline for the shared Phase 5 remaining edge-case ledger
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase4_mismatch_ledger.md`
  - `docs/plans/phase5_long_term_default_hardening_plan.md`
  - `docs/plans/phase5_long_term_fallback_note.md`
  - `docs/plans/phase5_token_and_reconnect_handling_note.md`
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/README.md`

## Status Reading

- 本文已从 skeleton 升级为首版 draft，但仍处于 Phase 5 `pre-freeze` 阶段。
- 本文不是 Phase 4 mismatch ledger 的替代品，而是给“已经能被诚实解释成 bounded difference 的剩余差异”留稳定账本。
- 当前表内条目仍是保守 seed rows，只记录已经有冻结边界、且当前更像长期边角差异而不是 switch blocker 的项目。
- 本文不宣告 Phase 4 已收口，也不宣告 `direct-default` 已经切换完成。

## 当前起点

- Phase 4 mismatch ledger 当前仍为空表；这不是“已经完全对齐”的声明，而是说明当前还不应凭主观印象提前登记 mismatch。
- Phase 5 的 long-term fallback note 与 token / reconnect handling note 已进入首版 draft，已经足够给 edge-case 记账提供边界约束。
- 当前 Layer 1 仍明确：`new_task` 之外的 direct send-side actions 没有进入默认路径；active detail sidecar 仍是读侧 freshness 辅助，mail / local-cache 仍是 truth / fallback 层。
- 当前仍有若干 Phase 4 / Phase 5 边界尚未 freeze，但 `hard_rejection_stop` 已按 shared negative closeout 读成 closed；它不再是 standing blocker，也不应被迁入本文当作 edge case。
- 因此，本文当前只记录“即使 Phase 4 收口后也大概率仍会存在、但已经有稳定解释边界”的差异，不提前吞并 blocker。

## 本文预期职责

- 记录 direct-versus-mail 之间仍会保留的、但已经被边界化的差异。
- 记录这些差异的 user impact 与 operator handling，避免后续把它们误判成新 mismatch。
- 给 shared review 一个诚实的“剩余不完全对齐项”列表，而不是伪装成“direct 与 mail 已完全等价”。
- 当某条差异超出本文定义边界时，反向指示它应退回 Phase 4 mismatch / rollback 路径，而不是继续停留在 edge-case ledger 里。

## 使用边界

- 尚未完成 Phase 4 bind / parity 关闭的样本，继续留在 `phase4_dual_stack_parity_checklist.md` 或 `phase4_mismatch_ledger.md`，不提前迁入本文。
- 只有当某个差异已经有稳定解释、稳定证据、且不再属于 `switch_blocker` / `fallback_required` 时，才允许进入本文。
- 同一类差异优先聚合成一条 bounded item，不因多个样本重复建多条。
- 本文当前首轮只覆盖 `new_task` 与 active detail sidecar 相关 edge cases。
- 本文不用于给 `reply`、`/status` 或其他 direct control flow 提前扩 scope。

## 字段形状冻结

本文继续固定使用以下字段：

- `edge_case_id`
- `covered_flow`
- `scenario`
- `direct_behavior`
- `mail_behavior`
- `user_impact`
- `operator_handling`
- `owner_repo`
- `current_status`
- `evidence`
- `notes`

`owner_repo` 当前建议只使用：

- `android`
- `mail_runner`
- `shared`

`current_status` 当前建议只使用：

- `open`
- `bounded`
- `accepted_for_phase5`
- `closed`

## 日常登记规则

- 先确认该问题不再属于 Phase 4 mismatch；如果仍可能阻塞 `direct-default`，优先留在 Phase 4 台账。
- 先确认同一类差异已经有稳定 evidence bundle；没有 stable evidence 的“感觉像 edge case”不直接入表。
- 先确认 direct 行为与 mail 行为都能被清楚描述；只会写一边、另一边仍模糊的差异不进入本文。
- `bounded` 用于“仓库侧已经能稳定解释，但 shared closeout 还没最终盖章”的条目。
- `accepted_for_phase5` 用于“双边都承认这是长期剩余差异，但它不再阻塞默认路径评估”的条目。
- `closed` 用于“该差异已经被实现收敛掉，或后续 freeze 已把它吸收为正常合同”的条目。

## Seed Rows

下表当前不是最终长期全集，而是第一批已经能被仓库侧诚实解释的 bounded seed rows：

| edge_case_id | covered_flow | scenario | direct_behavior | mail_behavior | user_impact | operator_handling | owner_repo | current_status | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `edge_case.new_task.accepted_ack_identity_deferred` | `new_task` | direct `packet_ack.accepted = true` 之后，最终 thread / session identity 仍需等待后续 mail outcome / canonical summary；这不是 accepted-path same-run closeout 仍未完成的意思 | ack 只保证 accepted、stable `receipt_id`，可选 `transport_message_id` 仅作观察；Android 不能从 ack 直接推断最终 session id | mail-first `new_task` 从第一封 ingress mail 起就有 durable mail-chain identity 锚点 | direct UI 不能把 ack 元数据当成最终 thread identity；即使 accepted-path same-run bind 已关闭，用户仍需等待后续 status mail / canonical summary 才能获得 canonical identity | 按当前冻结的 `request_id` -> `transport_message_id <-> ingress_message_id` -> `last_summary` 顺序对账；`terminal_mail_message_id` 只作为 canonical outcome supporting evidence，不是新的 bind level；只有后续 bind 真回归才退回 Phase 4 | `shared` | `bounded` | Phase 2 ack meaning、Phase 4 parity checklist、current canonical summary docs、Android `thread_097` / `thread_098` accepted-path closeout authority | 这不是 bug；它在 accepted-path same-run closeout 已完成后仍然成立，因为它描述的是 ack 语义边界，而不是 bind 缺口 |
| `edge_case.active_detail.gap_reverts_to_mail_truth` | active detail sidecar | direct detail sidecar 出现 reject、gap、或 apply failure | direct freshness 会暂时中断；Android 应保留当前 mail/local-cache detail，并通过 `detail_refresh` / `detail_reconnect` 重新获取 snapshot | mail / local-cache 继续作为 detail truth 与 fallback 层，不依赖 direct continuity | 用户可能暂时看不到最新 direct freshness，但不会失去 durable detail truth | 先按 Phase 3 wire / fixture 判断是否属于正常 gap recovery；只有 direct 投影错误覆盖了 mail truth 才升级为 mismatch | `shared` | `bounded` | Phase 3 wire gap/resubscribe rules、fixture `gap_resubscribe_fresh_snapshot`、current Android contract | 这是 direct freshness 与 mail truth 共存的固有边界，不应被误记成 send-path fallback |
| `edge_case.active_detail.identity_normalizes_after_resolve` | active detail sidecar | Android 用 `repo_path` / `workdir` 或 `thread_id` fallback subscribe，server 在 accepted snapshot 中回写 canonical workspace/session identity | direct sidecar 可先接受 fallback locator，再在 snapshot / update 中只返回 canonical `workspace_id` / `session_id` | mail path 更常直接沿现有 thread / capsule identity 阅读，不经历一次 subscribe-time identity normalization | 第一次 direct snapshot 可能把本地 detail route 从 fallback 标识收敛到 canonical identity；这应被视为正常归一，不是异常跳变 | 对照 Phase 3 wire / fixture 的 canonical identity 规则核对；只有 server 给出不一致 canonicalization 才升级为 mismatch | `shared` | `bounded` | Phase 3 wire business meaning、fixture package fallback-resolution rows | 当前这条差异属于 read-side normalization，不授权扩写成新的 workspace/history API |

## 当前不应迁入本文的项目

以下项目当前仍更像 Phase 4 blocker 或 mismatch 输入，而不是 bounded edge case：

- 如果后续又出现 latest direct / fallback evidence 到 PC canonical outcome 的 bind regression，它应退回 Phase 4 mismatch / rollback，而不是提前被吸收到长期 edge-case ledger。
- `hard_rejection_stop` 当前不应迁入本文；它已经有 shared negative closeout，后续若 current build 再出现 regression，应退回 Phase 4 mismatch / rollback。
- post-accept failure 的恢复动作仍应留在 rollback / mismatch 评审，而不是提前记成长期正常差异。
- 任何仍会触发 `switch_blocker` 或 `fallback_required` 的项目。

## 当前阻塞点

- Phase 4 还未形成 shared closeout，因此哪些 seed row 最终能升级为 `accepted_for_phase5` 还没有双边最终结论。
- `direct-default` 尚未稳定落地，因此“剩余 edge cases” 的最终集合还不能宣告收敛。
- 当前三份 Phase 5 文档虽然都已进入 draft，但 shared review 仍缺 Phase 4 evidence closeout 作为前提。
- 如果后续发现 seed row 实际仍会影响 switch gate，它必须退回 Phase 4，而不是继续留在本文。

## Exit Reading

这份 ledger 可以进入有效使用状态，至少要满足：

- Phase 4 mismatch ledger 已把 `switch_blocker` / `fallback_required` 项目分流清楚。
- `direct-default` 已经对 covered flow 生效，或至少已进入 shared review。
- 表内每条差异都能被双方解释成“已知、可边界化、且不再阻塞默认路径”的项目。
- 新出现的 direct-versus-mail 差异能够被明确判断：要么进入本文，要么退回 Phase 4 mismatch / rollback，而不是两边混账。
