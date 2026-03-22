# Phase 4 Mismatch Ledger

## Status

- Date: 2026-03-22
- Scope: repository-side skeleton plus first evidence readout for the shared Phase 4 mismatch ledger
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\phase4_mismatch_ledger.md`

## Status Reading

- 本文是 PC 侧对 `phase4_mismatch_ledger.md` 的首版 skeleton，加上第一轮仓库证据 readout。
- 当前仅针对 `new_task` 预留记录形状。
- 本文当前先冻结字段和严重度，不宣称已经发现或关闭了全部 mismatch。

## 当前 Readout

- 截至 2026-03-22，PC 仓库首轮 evidence review 与 repo-side matrix validation 未形成需要立即登记的 repo-side confirmed mismatch。
- `new_task` 的 accepted / fallback-classified rejection / hard rejection 三类 repo-side outcome 现在已有统一 classifier 与 packet store `last_error_code` 证据，但当前仍未观察到需要升格为 `fallback_required` 或 `switch_blocker` 的单边偏差。
- 当前空表不是“Phase 4 已全部对齐”的声明，只表示仓库侧现有测试和合同没有发现需要立刻升级为 `fallback_required` 或 `switch_blocker` 的单边问题。
- 当前 shared authority 已补齐：Android 发起 mail `new_task` fallback 的实际表现、同一 run summary 对账、fresh `daily_closeout_bundle` workflow reuse，以及 `request_id`-first bind 都已有样本；`hard_rejection_stop` 也已按 negative closeout 读成 closed，因此当前没有理由把它继续当成 standing mismatch seed 或 Phase 4 尾项。

## 严重度冻结

当前严重度先冻结为三档：

- `observe_only`
- `fallback_required`
- `switch_blocker`

## 字段形状冻结

本 ledger 先固定使用以下字段：

- `mismatch_id`
- `covered_flow`
- `scenario`
- `observed_behavior`
- `expected_behavior`
- `severity`
- `owner_repo`
- `current_status`
- `evidence`
- `rollback_impact`

`owner_repo` 当前建议只使用：

- `android`
- `mail_runner`
- `shared`

## 日常登记规则

- 先更新 parity checklist，再决定是否进入 ledger；没有 checklist row 的差异不直接登记。
- 没有完整 `daily_closeout_bundle` 的样本先留在 checklist，不创建 ledger item。
- `daily_closeout_bundle` 当前至少包含：PC canonical outcome artifact、supporting packet/journal evidence、Android latest send evidence、Android terminal-summary artifact。
- 对 `direct accepted` 行，只有 `request_id` 或 `transport_message_id <-> ingress_message_id` 的 same-run bind 已成立时，才允许把 `accepted_without_expected_outcome` / `summary_outcome_drift` 升级进 ledger。
- 若样本只剩 `last_summary token` 弱对齐，或 canonical outcome 尚未完成 bind，先记 checklist open note，不直接升为 mismatch。
- 只有同时具备 `observed_behavior` 与 `expected_behavior` 的 machine-readable evidence 时，才创建 ledger item；repo-side 单边日志或测试结论本身不直接构成 shared mismatch。
- `severity = observe_only` 用于已见漂移但暂不影响 fallback 可用性或 switch 判定。
- `severity = fallback_required` 用于 direct path 暂不能放大，但 mail fallback 仍必须继续保留且可执行。
- `severity = switch_blocker` 用于 user-visible summary、expected outcome、或 direct/mail 边界已经出现高风险漂移，必须阻止 `direct-default` 评估。

## Ledger

| mismatch_id | covered_flow | scenario | observed_behavior | expected_behavior | severity | owner_repo | current_status | evidence | rollback_impact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Notes

- 当前表格留空是刻意的：先把字段与严重度冻结，再由第一轮 parity evidence 回填实际 mismatch。
- 本轮 repo-side readout 后继续留空，同样是刻意的：当前更需要的是继续补双端证据，而不是凭主观感觉预登记“可能存在的差异”。
- 如果某条差异只靠“人工看日志感觉不对”，但没有 machine-readable evidence，优先先补证据口径，不要直接把它升级成 `switch_blocker`。
