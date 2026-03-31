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
- 截至 `2026-03-29`，当前 owner line 对 Android live read path 的读法已进一步收口：relay-visible `shared task_root` sync 只应视为 migration scaffold；当前下一条主线应切到 relay-native projection store，目标是在不改 Android contract 的前提下去掉 Android 读面对整棵 `task_root` 镜像的在线依赖
- 当前 repo-side 已经落地 `Phase 1` 的 Slice A-H first-pass 最小骨架；其中 `output_chunk` 已补到基于持久化 stream evidence 的 reconnect resend、显式 `output_resume_request`、fixture loopback selective replay，以及 websocket roundtrip 回归，`artifact_manifest` 也已补到基于真实 `artifact_index.json + artifact_file_binding_index.json` 的本地 truth-projection evidence，并进一步补上 `external_delivery_index.json`、live local relay `/v1/files` roundtrip evidence，以及真实 VPS relay `/v1/files` upload + metadata/content roundtrip evidence；`OpenCode SDK` 也已补上基于 `event` SSE 的 same-layer incremental message-part stream evidence。external-delivery 现在还新增了 `external_delivery_backend_preference=file_surface` cutover 开关，因此 `/v1/files` owner lane 已可在 `COS` 仍保留配置时显式优先启用；如果 deployment 里仍有超出 live `/v1/files` 上限的 artifact，当前 cutover 行为会只对这些 oversize artifact 保留 `COS` 兼容交付。`pc-control` 这边也已在真实 VPS relay 上补到 single-PC live `command_dispatch -> command_ack -> event -> result -> output_chunk`、`output_resume_request(after_seq=1)` selective replay，以及 `artifact_manifest(download_ref_source=external_delivery_index.file_surface)`；显式 `profile=default` 在 `Codex SDK` adapter 上的默认语义缺口也已修复并在真实链路上重新验证通过。`2026-03-26` 进一步补到 multi-PC live routing evidence：双 probe `pc_id` 同时在线时，定向 dispatch 已可稳定只命中目标连接，不再串投到另一条 websocket。除此之外，当前更应该继续收硬的是 live cutover/decommission 观察窗口与 consumer 验证；如后续还要扩 `pc-control` live 联调，应把更高层多 `PC` observer / subscription 侧需求单列，而不是继续重复证明 routing
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
- `vps_relay_projection_store_mainline_v0.1.md`
- `vps_relay_projection_store_schema_v0.1.md`
- `vps_relay_projection_publisher_protocol_v0.1.md`
- `vps_relay_projection_cutover_shadow_compare_v0.1.md`
- `vps_first_multi_pc_phase1_execution_plan_v0.1.md`
- `android_facing_environment_inventory_facade_requirements_v0.1.md`
- `android_facing_session_action_facade_requirements_v0.1.md`
- `android_facing_session_action_facade_preflight_checklist_v0.1.md`
- `repo_only_preflight_queue_v0.1.md`
- `vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`
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
- 近期开发目标是直接切入 `VPS-first` 主控制面，而不是设计长期 mail 共存
- Android live read path 当前主线应收敛到 relay-native projection store，而不是继续围绕 relay-visible `shared task_root` 整树同步做长期设计
- 一旦任务从“定方向”进入“准备开工”，应继续读 projection store 的 schema、publisher protocol 与 cutover/shadow compare 三份执行级文档，而不是在代码阶段临时补规则
- mail 在规划层只作为 cutover 前 migration / export / notification / compatibility 基础设施；默认目标是 cutover 后退场，而不是长期保留
- artifact external delivery 在规划层应默认收敛到 `VPS /v1/files`；`COS` 当前可作为 cutover 前兼容 lane 暂留，但默认目标是 `/v1/files` 稳定后退场

如果任务已经从“讨论架构”进入“开始编码 Phase 1”，继续读：

- `vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`

如果任务已经进入“准备 `/v1/files` cutover / `COS` 退场”的运维与计划收口，继续读：

- `vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`

如果任务还需要判断“今天代码实际上已经推进到哪”，再补读：

- `../../state.md`
- `../reference/pc_control_plane_fixture_smoke_validation.md`
- `../reference/pc_control_live_smoke_validation.md`
- `../reference/sdk_stream_smoke_validation.md`
- `../reference/artifact_contract_smoke_validation.md`

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
- 如果任务是“继续推进 Android live read path / relay 读层收敛”，先看 `vps_relay_projection_store_mainline_v0.1.md`
- 如果任务是“准备直接开工 projection store”，继续顺序读：
  - `vps_relay_projection_store_schema_v0.1.md`
  - `vps_relay_projection_publisher_protocol_v0.1.md`
  - `vps_relay_projection_cutover_shadow_compare_v0.1.md`
- 如果任务是“解释旧 direct 行为什么样来的”，再回看“当前兼容 / closeout 参考线”
