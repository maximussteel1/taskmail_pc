# Phase 4 Rollback Trigger Note

## Status

- Date: 2026-03-22
- Scope: repository-side first validated trigger baseline for the shared Phase 4 rollback trigger note
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase4_mismatch_ledger.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\phase4_rollback_trigger_note.md`

## Status Reading

- 本文是 PC 侧对 `phase4_rollback_trigger_note.md` 的首轮仓库验证基线。
- 当前仅覆盖 `new_task`。
- 本文冻结的是 trigger 形状与当前建议起点，并补入了 PC 仓库当前已经具备的 trigger 证据来源，包括 outcome classifier 与 packet store `last_error_code`；它仍不是最终实现状态。

## 当前 trigger 形状冻结

每条 rollback trigger 至少写清：

- `trigger_id`
- `covered_flow`
- `trigger_condition`
- `evidence_source`
- `action`
- `blocks_primary_path_switch`
- `status`
- `notes`

## Trigger 初稿

| trigger_id | covered_flow | trigger_condition | evidence_source | action | blocks_primary_path_switch | status | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| new_task.bootstrap_failure | `new_task` | bootstrap 未到达可发送 direct packet 的前置状态 | bootstrap probe result、relay connect evidence、transport failure receipt | 当前发送回退到 mail；重复出现时进入 switch review | yes | `pc_repo_boundary_confirmed` | 见 R1 |
| new_task.ack_classification_drift | `new_task` | `packet_ack` 分类与当前 authority / fallback matrix 不一致 | `packet_ack.accepted`、error code / reject classification、shared parity checklist | 暂停扩大 direct-default；必要时回退到 mail-default | yes | `pc_repo_boundary_confirmed` | 见 R2 |
| new_task.accepted_without_expected_outcome | `new_task` | direct accepted 后长期拿不到预期 thread / status mail outcome | parity checklist、mail outcome evidence、runtime state / packet store evidence | 记入 mismatch ledger；在证据闭环前阻止扩大 switch 范围 | yes | `pc_repo_evidence_present` | 见 R3 |
| new_task.fallback_path_unavailable | `new_task` | direct 失败后 mail fallback 也不可用 | direct failure evidence、mail send failure evidence、outbound journal | 客户端保留 draft 并显示实际错误；阻止 primary-path switch | yes | `pc_repo_boundary_confirmed` | 见 R4 |
| new_task.summary_outcome_drift | `new_task` | user-visible summary / terminal outcome 与 mail baseline 高风险漂移 | workspace/detail summary evidence、mail outcome evidence、canonical status-mail baseline | 记入 mismatch ledger；必要时改回 mail-default | yes | `pc_repo_evidence_present` | 见 R5 |

## 稳定消费顺序

- 第一步先看 parity checklist；没有对应 checklist row，就不直接判定 trigger。
- 第二步先确认 `direct accepted` / `fallback_to_mail` 样本已经形成 checklist 里的 `daily_closeout_bundle`；没有同一 run 的 bundle，就不直接给 primary-path switch 下结论。
- 第三步只把 confirmed drift 升级到 mismatch ledger；弱绑定样本或 `historical_gap` 不单独触发 rollback。
- 第四步再按 trigger table 判定动作；对 `direct accepted` 行，same-run bind 顺序固定为：`request_id` -> `transport_message_id / ingress_message_id` -> `last_summary token` 弱回退。
- 若 `direct accepted` 行的 bind 还没闭环，只记 evidence gap，不直接判 `accepted_without_expected_outcome` 或 `summary_outcome_drift`；但该样本也不能作为 `direct-default` 的正向输入。
- `hard_rejection_stop` 是 negative closeout，不按 `daily_closeout_bundle` / same-run bind 关单；它以 relay hard rejection evidence、Android `local stop + draft retention`、`no implicit mail fallback`，以及无新 adjacent-runtime thread 判定。
- 只有在 bound sample 上真的出现 expected outcome 缺失或 summary conflict，才升级到 mismatch / rollback review。
- 只要存在 open 的 `fallback_required` 或 `switch_blocker` 级别 mismatch，`new_task` 就不得进入 `direct-default` 评估。

## 仓库侧 trigger 证据摘录

### R1 Bootstrap Failure

- `docs/plans/phase2_direct_outbound_contract_v1.md` 已冻结：relay config missing、`healthz` failure、connect timeout、`hello` 未到达 `hello_ack` 时，Android 应回退到当前 mail `new task` 路径。
- `tests/test_outbound_relay_transport.py::test_relay_transport_returns_failure_receipt_when_not_configured` 证明仓库侧当前会产生 `receipt.success = false`、`transport_message_id = None` 和明确的 `error_message`，可作为 bootstrap / transport failure 的 machine-readable evidence。

### R2 Ack Classification Drift

- `docs/plans/phase2_direct_outbound_contract_v1.md` 已冻结 `unsupported_action` / transient rejection 与 hard rejection 的 fallback 边界。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_returns_unsupported_action_for_non_new_task_phase2_payload` 证明 capability rejection 仍落在 `unsupported_action` 分类。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_returns_invalid_payload_for_missing_task_text` 证明 `invalid_payload` 仍落在 hard rejection 分类。
- `mail_runner/relay_server/direct_actions.py::classify_direct_new_task_server_outcome(...)` 已把 accepted / fallback-classified rejection / hard rejection 收敛成一个 repo-side 归一入口，当前未发现 classifier 与 fallback matrix 漂移。
- 只要 Android 实际观测到的 ack 分类与这些边界不一致，就应先把问题记入 parity checklist / mismatch ledger，而不是继续扩大 direct-default。

### R3 Accepted Without Expected Outcome

- `tests/test_relay_server_direct_actions.py::test_direct_new_task_packet_is_accepted_and_reuses_mail_task_start_path` 证明 happy path 下，direct accepted 不只是返回 ack，还会落到 `delivery_status = delivered`、thread `status = done`，并沿 canonical mail 路径发送 `[ACCEPTED]` / `[RUNNING]` / `[DONE]`。
- 因此，后续如果出现 `packet_ack.accepted = true` 但长期拿不到预期 thread / status mail outcome，不应按“偶发观察噪音”处理，而应立即进入 mismatch ledger 和 switch gate 评审。

### R4 Fallback Path Unavailable

- `tests/test_outbound_service.py::test_send_status_update_falls_back_to_email_when_relay_fails` 证明 repo 内既有 relay -> email fallback 会在 journal 中留下 `["relay", "email"]` 的尝试序列，且能区分首跳失败和 fallback 成功。
- `tests/test_outbound_service.py::test_send_status_update_records_failed_delivery_attempt` 证明 mail send 失败也会留下 `transport_name = "email"`、`success = false`、`error_message = "RuntimeError: smtp down"` 的 machine-readable evidence。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_records_post_accept_fallback_classified_failure_on_packet_store` 与 `tests/test_relay_server_packet_store.py::test_persistent_packet_store_persists_error_code_for_failed_delivery` 证明 post-accept `direct_temporarily_unavailable` 现在会落到 packet store `last_error_code`，这让 rollback trigger 不再只靠错误文本判断。
- 因为 direct failure 后重新发起 mail `new_task` fallback 的动作仍由 Android 侧执行，所以这条 trigger 在 PC 侧当前主要用于冻结“失败必须可记账、且不得假装成功”的回退规则。

### R5 Summary Outcome Drift

- `tests/test_app_phase2.py::test_process_once_runs_new_task_happy_path` 给出了当前 canonical mail `new_task` 基线：成功创建 thread，并产出 `[ACCEPTED]` / `[RUNNING]` / `[DONE]` 与 `status = done`。
- `tests/test_relay_server_direct_actions.py::test_direct_new_task_packet_is_accepted_and_reuses_mail_task_start_path` 证明 direct accepted happy path 当前复用了同一套业务结果语义。
- 因此，只要 Android / PC 对同一 run 的 user-visible summary 或 terminal outcome 与上述 mail baseline 出现高风险漂移，就应阻止 primary-path switch，而不是先扩大 covered flow。

## Notes

- `status` 暂使用：
  - `pc_repo_evidence_present`：PC 仓库已有测试、合同或状态证据，可直接支撑 trigger 判断。
  - `pc_repo_boundary_confirmed`：PC 仓库已冻结触发边界，但最终 fallback 动作或 UI 呈现仍由 Android 侧负责。
- 当前 trigger note 的重点是把“什么时候只记账、什么时候回退、什么时候阻塞 switch”写成显式规则。
- 如果后续要把 `reply` 或 `/status` 纳入 trigger note，必须先有新的 contract freeze。
