# Plans

本目录存放 `mail_based_task_manager` 的仓库内计划、当前主线、后继候选线、冻结线，以及 closeout / handoff / evidence 文档。

## 规则

- `docs/current/*` 永远比本目录更高优先级
- 如果 `README.md`、`state.md`、`docs/current/*` 与本目录冲突，以 `docs/current/*` 为准
- 本目录只描述当前仓库的实现计划与 closeout，不承载未来平台的总设计
- 已完成切片不要通过继续改旧 execution plan 的方式“隐式重开”；如果要开启新一轮实现，应新建 plan 或 handoff

## 当前状态

- 当前 repo-side 主线仍是 `TaskMail direct relay/control/file`
- 这条线已经把较大一段 current behavior 落到代码和 `docs/current/*`，但整条线尚未闭环
- `phase2/phase3/phase4`、post-creation、taskmail control/file 相关文档当前仍是这条主线的 closeout / evidence / handoff 支撑资料，不应一律当作纯历史
- `P9 HTML` 仍处于冻结状态，不是当前默认编码队列
- `VPS ingress truth v1` 当前是后继候选线，不是当前主线

## 当前阅读顺序

1. 先看当前行为：`docs/current/*`
2. 再看本目录中的“当前主线”
3. 如果要补当前主线的 closeout / parity / bind 证据，再看本目录中的“主线支撑文档”
4. 冻结线只在明确 reopen 时才重新进入 active queue

## 当前主线

当前 active mainline 是 `TaskMail direct relay/control/file`。

当前主线阅读入口：

- `android_pc_vps_coordinated_execution_plan.md`
- `phase4_dual_stack_parity_plan.md`
- `post_creation_session_action_contract_v1.md`
- `post_creation_session_action_execution_plan.md`
- `post_creation_session_action_closeout_handoff.md`
- `taskmail_bootstrap_control_contract_v2.md`
- `taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `taskmail_artifact_fileid_mapping_sidecar_note_v0.1.md`
- `taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `taskmail_transport_probe_payload_companion_note_v0.1.md`
- `android_transport_probe_joint_debug_requirements.md`
- `taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`

当前读法：

- direct `new_task`、bootstrap `[SYNC]` `v1/v2`、current-session direct `/status` / plain `reply`、active-detail sidecar、`/v1/files`、`canonical_summary.json` / `session_action_closeout.json` / closeout bundle 都已经有 current behavior 落地
- Android 仓当前也已补上 transport readiness / observability 的首轮真机正向证据：`/control transport_probe` same-id replay 稳定，`/v1/files` 已有 debug-only 单样本文本文件闭环
- 但这条线还没有被仓库文档正式读成“闭环完成”；相关 closeout、live acceptance、Layer 1 读法升级判断仍可能继续发生

## 后继候选线

当前明确排在这条主线之后的候选内容是：

- `vps_ingress_truth_v1_checklist.md`
- `vps_ingress_truth_v1_execution_order.md`

补充说明：

- “是否升级 current-session direct `/status` / plain `reply` 的 Layer 1 读法” 目前还是一个待决策方向，不是已冻结的新执行计划
- 如果 owner 决定把这条 decision line 升格成新切片，可以新写 plan/handoff，也可以继续沿当前主线 closeout 资料推进；不要先把它误降级为“已经结束的旧线”

## 冻结或非当前队列

以下文档当前不是 active implementation queue：

- `p9_html_mail_projection_plan.md`
- `android_consumer_contract_alignment_plan.md`
- `android_consumer_protocol_freeze_note.md`
- `android_consumer_acceptance_requirements.md`
- `outbound_mail_contract_convergence_plan.md`
- `phase5_long_term_default_hardening_plan.md`
- `phase5_long_term_fallback_note.md`
- `phase5_token_and_reconnect_handling_note.md`
- `phase5_remaining_edge_case_ledger.md`
- `phase5_freeze_review_precheck.md`

这些文档当前分别属于：

- 冻结中的 HTML / consumer-contract 线
- 当前主线之外的长期 hardening / freeze-prep / evidence 资料

## 主线支撑文档

### 1. Relay Bootstrap / Baseline

- `vps_relay_bootstrap_plan.md`
- `vps_relay_deploy_runbook.md`
- `vps_environment_baseline.md`
- `phase0_public_plaintext_baseline.md`
- `phase0_relay_readiness_note.md`
- `phase0_direct_connect_handoff.md`
- `phase1_direct_connect_bootstrap.md`

### 2. Direct `new_task` / Active Detail / Phase 4 Evidence

- `android_pc_vps_coordinated_execution_plan.md`
- `phase2_direct_outbound_contract_v1.md`
- `phase2_direct_outbound_closeout_handoff.md`
- `phase3_direct_inbound_mapping_v1.md`
- `phase3_direct_inbound_wire_v1.md`
- `phase3_direct_inbound_fixture_package_v1.md`
- `phase3_direct_inbound_closeout_handoff.md`
- `phase4_dual_stack_parity_plan.md`
- `phase4_dual_stack_parity_checklist.md`
- `phase4_mismatch_ledger.md`
- `phase4_rollback_trigger_note.md`

### 3. Post-Creation Direct Action Closeout

- `post_creation_session_action_contract_v1.md`
- `post_creation_session_action_execution_plan.md`
- `post_creation_session_action_closeout_handoff.md`

### 4. TaskMail Control / File / Replay / Auth Notes

- `taskmail_bootstrap_control_contract_v2.md`
- `taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `taskmail_artifact_fileid_mapping_sidecar_note_v0.1.md`
- `taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `taskmail_transport_probe_payload_companion_note_v0.1.md`
- `android_transport_probe_joint_debug_requirements.md`
- `taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`

## 仍可用的支撑性参考文档

以下文档不是当前主线本身，但仍是有效参考：

- `pc_background_hardening_plan.md`
- `pc_service_hosting_plan.md`
- `mail_adapter_refactor_plan.md`
- `run_artifact_delivery_plan.md`
- `artifact_markdown_rendering_plan.md`
- `backend_permission_control_plan.md`
- `project_folder_sync_entry_plan.md`
- `project_folder_sync_relay_single_account_plan.md`

## 使用建议

- 如果任务是“修改当前行为”，先改 `docs/current/*`
- 如果任务是“判断当前还缺什么”，先看 `docs/plans/coding_backlog.md`
- 如果任务是“继续推进当前直连主线”，优先读本文件里的“当前主线”和“主线支撑文档”
- 如果某份旧计划文档没有被本文件列为当前主线、后继候选线或冻结线，就不要默认把它当成当前实现队列
