# Phase 5 Long-Term Default Hardening Plan

## Status

- Date: 2026-03-22
- Scope: repository-side pre-freeze execution plan for the Phase 5 long-term default hardening documentation set
- Layer: Layer 2 repository plan
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase4_mismatch_ledger.md`
  - `docs/plans/phase4_rollback_trigger_note.md`
  - `docs/plans/phase5_long_term_fallback_note.md`
  - `docs/plans/phase5_token_and_reconnect_handling_note.md`
  - `docs/plans/phase5_remaining_edge_case_ledger.md`
  - `docs/plans/phase5_freeze_review_precheck.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`

## Purpose

把 `android_pc_vps_coordinated_execution_plan.md` 里的 Phase 5 intent 展开成“仓库侧文档工程下一轮该按什么顺序落地”的可执行计划。

这份计划当前的重点不是宣布 Phase 5 已经开始默认切换，而是：

- 给三份 Phase 5 shared artifacts 一个明确的填充顺序
- 把 Phase 4 closeout 与 Phase 5 freeze 的边界写清
- 避免后面把尚未收口的 Phase 4 mismatch 误记成 Phase 5 edge case

## Current Starting Point

当前仓库侧已经具备以下前提：

- Phase 4 的 repository-side execution plan 已存在
- Phase 4 的 parity checklist、mismatch ledger、rollback trigger note 已存在 first evidence baseline
- `new_task` 的 repo-side accepted / fallback-classified rejection / hard rejection classifier 已落地
- `canonical_summary.json` 已提供 `request_id`、`ingress_message_id`、`terminal_mail_message_id`、`terminal_mail_subject` 等对账锚点
- Phase 5 的三份 shared artifact 已有稳定落点：
  - `phase5_long_term_fallback_note.md` 已进入首版 draft
  - `phase5_token_and_reconnect_handling_note.md` 已进入首版 draft
  - `phase5_remaining_edge_case_ledger.md` 已进入首版 draft
  - `phase5_freeze_review_precheck.md` 现已把“哪些项已具备 freeze review preparation 形状、哪些 open lines 仍必须保留”写成显式 precheck

但当前仍明确存在这些限制：

- Phase 4 的 cross-repo matrix 尚未宣告 closeout
- `direct-default` 还没有被双方共同确认已经对 covered flow 生效
- `thread_097` / `thread_098` 已把 shared artifact workflow reuse 与 `request_id`-first automatic bind 提升到 shared authority readout，但这不等于立即授权切换 `direct-default`
- `new_task.hard_rejection_stop` 现在应读作已完成 shared negative closeout：
  - repo-side hard rejection classifier 与 no-implicit-fallback 语义已验证
  - Android authority 已保留 relay hard rejection、`local stop + draft retention`、以及 `thread_085` 之后无新 adjacent-runtime thread 的 closeout evidence
  - 这条线不再要求 `daily_closeout_bundle` 或 `request_id`-first bind，因为它不是 accepted-path same-run closeout
- 当前仓库内 `thread_093` / `thread_094` / `thread_095` 的 closeout bundle 更适合作为 backfill / helper evidence；shared artifact workflow reuse 与 request_id-first automatic bind 的最终关口仍应以 Android 侧已保留的 `thread_097` / `thread_098` closeout authority 为准

因此，当前 Phase 5 应读作“先做文档装配与 freeze 前准备”，而不是“已经可以写最终长期默认口径”。

## Phase 5 Goal

Phase 5 的仓库侧文档目标是把“被选中的 direct default 如何长期稳定运行”写成可审阅的 shared explanation，至少做到：

1. 可持续：fallback 不是临时保命，而是长期真实可运行路径
2. 可运维：token、reconnect、stale-session、replay 不靠口口相传
3. 可解释：剩余 direct-versus-mail 差异能被诚实记账，而不是伪装成完全对齐
4. 可收口：只有 covered flow 的默认直连已稳定，Phase 5 才能进入 freeze

## Shared Artifacts In This Phase

按当前总计划，Phase 5 首轮文档工程的 shared artifacts 是：

1. `phase5_long_term_fallback_note.md`
2. `phase5_token_and_reconnect_handling_note.md`
3. `phase5_remaining_edge_case_ledger.md`

当前三份 shared artifacts 都已进入 pre-freeze draft，但都还不是 freeze 版本。

三者职责分别是：

- long-term fallback note：定义长期 direct-default 之后 fallback 仍应如何保持真实
- token and reconnect handling note：定义 token / reconnect / replay / stale-session 的长期解释边界
- remaining edge-case ledger：记录双方都承认仍存在但已被边界化的 edge cases

## Entry Gate

开始填充 Phase 5 文档，不要求先完成全部 Phase 4 closeout；但至少要满足：

- Phase 4 artifact names 已冻结
- covered flow 仍只收窄为 `new_task`
- 当前 shared scope 不额外扩展到 `reply` / `/status`

但如果要把 Phase 5 文档从当前 `pre-freeze` 状态升级为 freeze，则仍必须满足：

- Phase 4 cross-repo matrix 已经闭环
- `direct-default` 的 covered-flow 边界已被双方确认
- Phase 4 mismatch ledger 中不存在未界定归属的高严重度 open item

## Work Packages

### WP1：冻结 Phase 5 文档边界

目标：先写死“Phase 5 讨论的是什么，不讨论什么”，避免把未收口的 Phase 4 问题提前包进来。

输出：

- 本计划
- 三份 shared artifact 的边界说明

至少应锁定：

- Phase 5 仍只围绕 `new_task` direct default 和 active detail sidecar 辅助语义展开
- 不能把 `reply` / `/status` 提前写成长期默认路径
- 不能把 Android UI 重构细节直接写进 PC / VPS 业务合同

### WP2：填充长期 fallback note

目标：把“默认直连之后 fallback 还必须是真的”写成明确规则。

输出：

- `phase5_long_term_fallback_note.md` 首版 draft

至少应覆盖：

- bootstrap failure
- fallback-classified rejection
- post-accept failure
- fallback path unavailable

约束：

- 继续尊重 `packet_ack.accepted = true` 后禁止 duplicate mail fallback 的既有边界
- 不把 hard rejection 改写成“也可以自动降级到 mail”

### WP3：填充 token / reconnect handling note

目标：把长期运行下最容易靠经验传递的 direct 行为收敛成可审阅文字。

输出：

- `phase5_token_and_reconnect_handling_note.md` 首版 draft

至少应覆盖：

- transport token ownership / rotation expectation
- `unauthorized` surface
- bootstrap reconnect
- sidecar resubscribe / stale-session
- `packet_id` / `request_id` replay 边界

约束：

- 不扩 scope 到新 direct action
- 不把凭证存储实现细节写成跨仓 freeze

### WP4：建立 remaining edge-case ledger

目标：把“仍存在但已被边界化”的 direct-versus-mail 差异单独建账。

输出：

- `phase5_remaining_edge_case_ledger.md` 首版 draft

至少应明确：

- 哪类问题仍属于 Phase 4 mismatch，不应迁入 Phase 5
- 哪类问题已经可以视为 bounded edge case
- edge case 的 user impact 和 operator handling 如何写

约束：

- 只有双方都承认且证据充分的问题才能进入本文
- 不能用 Phase 5 ledger 去掩盖仍会阻塞 `direct-default` 的 open mismatch

### WP5：定义 pre-freeze 到 freeze 的升级条件

目标：避免三份文件一直停在 `pre-freeze`，但没人知道什么时候能 freeze。

输出：

- 三份文档中的 `Exit Reading` 与本计划的统一升级规则

至少应明确：

- 哪些条件属于“可以继续写 draft”
- 哪些条件属于“可以进入 shared review”
- 哪些条件属于“可以宣告 freeze”

## Validation Strategy

本轮以文档工程为主，建议按以下顺序验证：

1. 先更新 `docs/plans/` 文档工件
2. 再确认 `docs/plans/README.md` 已挂到当前文档入口
3. 如果文档引用了新的 Layer 1 字段或证据，确认 `docs/current/` 已存在对应口径
4. 只有当文档推进伴随实现或测试口径变化时，才补 targeted tests 或 full suite

## Non-Goals

本计划当前明确不做：

- 宣告 Phase 4 已完成 closeout
- 直接宣布 `new_task` 已切到 `direct-default`
- 把 `reply` / `/status` 提前纳入长期默认范围
- 代替 Android 侧去定义 UI 细节
- 把 broader outbound convergence、P9、scheduler 改造混进当前阶段

## Exit Reading

Phase 5 文档工程在仓库侧可以读作“准备进入 freeze review”，至少要满足：

- 三份 shared artifacts 至少都已升级成可审阅 draft
- Phase 4 与 Phase 5 的边界在文档里已经清晰，不再混账
- fallback、token / reconnect、remaining edge cases 三条线都有稳定的 shared 落点
- 双方都知道哪些项仍等待 Phase 4 closeout，哪些项可以继续推进到 Phase 5 review

## Recommended Next Session Order

如果下一次继续推进，建议按以下顺序开工：

1. 先消费 `phase5_freeze_review_precheck.md`，明确哪些 line 已具备 freeze review preparation 形状、哪些 open line 仍必须保留
2. 对 precheck 中仍打开的 line 继续做 shared review preparation，尤其不要吞掉 fresh VPS acceptance
3. 如 fallback / token / edge-case 三份文档在 shared review 中再暴露 drift，再回到对应文档做最小修订；否则不重复开启已收口的 `hard_rejection_stop` 讨论
4. 若后续 current formal-host build 再次出现 hard rejection regression，再把它退回 Phase 4 mismatch / rollback；否则不重复开启同名尾项
5. 只有在 open lines 真正收窄到可接受边界后，才讨论是否进入 freeze review
