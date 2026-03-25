# Plans

本目录存放 `mail_based_task_manager` 的仓库内计划、当前主线、兼容/closeout 参考线、冻结线，以及 closeout / handoff / evidence 文档。

## 规则

- `docs/current/*` 永远比本目录更高优先级
- 如果 `README.md`、`state.md`、`docs/current/*` 与本目录冲突，以 `docs/current/*` 为准
- 本目录回答“仓库接下来按什么主线推进”，不是“今天代码已经长成什么样”
- 已完成或已退役的切片不要通过继续改旧 execution plan 的方式隐式重开

## 当前状态

- 当前代码行为仍是 mail-first
- 当前 future-direction active mainline 已切到 `VPS-first 多 PC 控制面`
- 旧的 `TaskMail direct relay/control/file` 不再是未来主线；它现在是 current-behavior migration reference 与 closeout 材料
- `VPS ingress truth v1` 不再单独读成“当前主线之后的后继候选线”，而应读成新主线下可复用的前置参考

## 当前阅读顺序

1. 先看当前行为：`docs/current/*`
2. 再看主线 authority：`android_pc_vps_evolution_authority.md`
3. 再看当前主线 owner note：`vps_first_multi_pc_control_plane_mainline_v0.1.md`
4. 如果需要跨仓控制面设计，再看：
   - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
   - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-multi-pc-control-plane-v0.1.md`
5. 如果需要理解旧 direct 线的 closeout / parity / evidence，再回看 legacy docs

## 当前主线

当前 active mainline 是 `VPS-first 多 PC 控制面`。

当前主线阅读入口：

- `android_pc_vps_evolution_authority.md`
- `vps_first_multi_pc_control_plane_mainline_v0.1.md`
- `vps_first_multi_pc_phase1_execution_plan_v0.1.md`
- `vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-multi-pc-control-plane-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-legacy-mail-to-control-plane-mapping-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-command-event-payload-appendix-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-result-artifact-errorcode-appendix-v0.1.md`

当前读法：

- 规划目标是 `VPS` 统一控制面、多 `PC` 节点执行、`pc-scoped workspace`
- 长期协议应收敛到 `command / event / output_chunk / result / artifact`
- mail 若保留，只作为 backup / export / notification / compatibility 基础设施

如果任务已经从“讨论架构”进入“开始编码 Phase 1”，继续读：

- `vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`

## 当前兼容 / closeout 参考线

以下文档继续有效，但它们不再是当前 future-direction owner line：

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

这些文档现在的正确读法是：

- current behavior 对应的 closeout / evidence / migration reference
- 不再是当前主线 owner queue

## 仍可用的前置参考文档

以下文档在新主线下仍然有参考价值：

- `vps_ingress_truth_v1_checklist.md`
- `vps_ingress_truth_v1_execution_order.md`
- `pc_background_hardening_plan.md`
- `pc_service_hosting_plan.md`
- `run_artifact_delivery_plan.md`

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

## 使用建议

- 如果任务是“修改当前行为”，先改 `docs/current/*`
- 如果任务是“判断现在的未来主线是什么”，先看 `android_pc_vps_evolution_authority.md`
- 如果任务是“继续推进当前主线”，优先读本文件里的“当前主线”
- 如果任务是“解释旧 direct 行为什么样来的”，再回看“当前兼容 / closeout 参考线”
