# VPS-First 多 PC Phase 1 实施计划（v0.1）

## Status

- Date: 2026-03-25
- Scope: repository-side Phase 1 execution plan under the `VPS-first multi-PC control plane` mainline
- Source of truth:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-multi-pc-control-plane-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-legacy-mail-to-control-plane-mapping-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-command-event-payload-appendix-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-result-artifact-errorcode-appendix-v0.1.md`

## 1. 目标

Phase 1 的目标不是完成整个 `VPS-first` 切换，而是先落成最小可运行骨架：

- `PC` 能注册为节点
- `VPS` 能看到节点和 workspace 清单
- `PC` 能上报自己支持的 `backend/profile/permission` 组合
- `VPS` 能下发结构化命令
- `PC` 能回传 `command_ack / event / result`
- `result` 能回传实际生效的 `backend/profile/permission/model`
- 可选流式输出 `output_chunk` 进入正式协议

换句话说，Phase 1 要先把“控制面骨架”立住，尽快把仓库推进到可直接切入 `VPS-first` 的状态；mail 在这阶段只作为 cutover 前兼容层保留，不再按长期并存目标设计。

## 2. 非目标

Phase 1 明确不做：

- 不做跨 PC 热迁移
- 不做共享 workspace 的多 PC 执行
- 不做 repo/worktree/native session 迁到 VPS
- 不做完整多用户 ACL
- 不要求在本阶段立即删除全部 mail 资产
- 不做 mail 长期共存或长期 fallback 架构设计
- 不要求 Android 在本阶段就切完 UI 与协议

## 3. 前置读法

在 Phase 1 中，文档读法应固定为：

- 当前行为：`docs/current/*`
- 未来主线 authority：`docs/plans/android_pc_vps_evolution_authority.md`
- 当前主线 owner note：`docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
- 控制面全量字段冻结：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
- 协议草案：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
- 执行策略附录：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`
- 旧 mail 语义映射附录：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-legacy-mail-to-control-plane-mapping-v0.1.md`
- `command / event` payload 附录：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-command-event-payload-appendix-v0.1.md`
- `result / artifact / error_code` 附录：`E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-result-artifact-errorcode-appendix-v0.1.md`

当任务进入 repo-side 编码阶段时，再继续读：

- `docs/plans/vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
- `docs/plans/vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`

这两份文档的作用不是替代字段 authority，而是把 Phase 1 第一刀真正落到：

- 模块归属
- store 设计
- handler 边界
- 验证层次

当前 repo-side 落地快照（`2026-03-25`）：

- Slice A：已落地最小 `pc_hello / hello_ack / heartbeat / connection_epoch` 协议、store、runtime 与测试骨架
- Slice B：已落地 `workspace_snapshot`、workspace inventory store、PC 侧 snapshot 上报与 fixture 骨架
- Slice C：已落地 `execution_policy` 能力快照、dispatch 前策略校验与显式拒绝路径
- Slice D：已落地最小 `command_dispatch -> command_ack` 骨架，已覆盖 `accepted`、`accepted_but_queued` 与首批 `unsupported_*` 拒绝语义
- Slice E：已落地 canonical `event`、`event_id` 去重、`running/accepted/queued/done` 首批时间线，以及 `effective_execution` 回填
- Slice F：已落地 canonical `result`、`result_id` 去重、`command_id` 单一 canonical result 与最小 `structured_payload`
- Slice G：已落地 first-pass canonical `output_chunk` packet、`stream_id + seq` 去重、最小 client/runtime/store/test/fixture 闭环、基于已落盘 `stream.events.jsonl` 的 reconnect resend、显式 `output_resume_request` / server-driven resume、fixture loopback selective replay，以及 websocket roundtrip 回归；`OpenCode SDK` 也已补上最小 same-layer persisted stream evidence。当前剩余 gap 主要收窄到更高层多 `PC` 路由/订阅证据，以及 `OpenCode` true incremental streaming 的后续增强验证
- Slice H：已落地 first-pass canonical `artifact_manifest` packet、最小 artifact metadata 回填，以及基于真实 `artifact_index.json + artifact_file_binding_index.json` 的本地 truth-projection evidence；successful external delivery 现在还会下落 `external_delivery_index.json`，并已补上 live local relay `/v1/files` roundtrip evidence、真实 VPS relay `/v1/files` upload + metadata/content roundtrip evidence，以及 live deployment 下 `22 MiB -> file_surface` / `34 MiB -> cos` 的真实业务样本。当前 planning 读法应改成：`VPS /v1/files` 是 artifact external-delivery owner lane，`COS` 只暂时保留为 cutover 前兼容线；repo-side 现在也已补上 `external_delivery_backend_preference=file_surface`，因此 owner-lane cutover 不再要求先移除 `COS` 配置。当前剩余 gap 已不再是“有没有 live COS 样本”，而是 cutover 观察窗口与退场门槛是否收硬
- single-PC live bring-up：已新增真实 `pc_control_live_smoke`，当前 public relay `/pc-control` 已补到 `pc_hello -> hello_ack`、`workspace_snapshot` 进入 `/healthz.pc_control` 视图，以及 reconnect 后 `stale_connection_epoch` fencing
- repo-side / live dispatch injection：已新增 operator-only `POST /debug/pc-control/dispatch` 与 `scripts/pc_control_operator_dispatch.py`，并已把这条入口真实部署到 VPS relay。当前 live `pc-home / workspace_969e9b323b70` 已补到 `command_dispatch -> command_ack(accepted) -> event(accepted/running/done) -> result(done) -> output_chunk(seq=1..5)`。联调过程中还显式暴露并修复了一个实现缺口：`Codex SDK` adapter 曾把显式 `profile=default` 错当成“必须查 profile 映射”；repo-side 已改成 `default -> unset profile` 语义，并已在真实链路上重新验证通过
- single-PC live roundtrip：已新增 `scripts/pc_control_live_roundtrip_smoke.py`。当前 public relay `/pc-control` 已补到 `output_resume_request(after_seq=1)`、reconnect selective replay、以及 `artifact_manifest(download_ref_source=external_delivery_index.file_surface)` 的真实 store-level evidence
- multi-PC live routing：已新增 `scripts/pc_control_live_multi_pc_smoke.py`。当前 public relay `/pc-control` 已补到双 probe `pc_id` 并发在线、双独立 `workspace_id` 注册、以及定向 dispatch A/B 只命中目标连接的真实 store-level evidence；远端 `commands.json` 已可同时保留两条命令的正确 `pc_id / workspace_id / ack / event / result`

## 4. Phase 1 切片顺序

### 4.1 Slice A: `PC` 节点身份与连接代次

目标：

- 固定 `pc_id`
- 固定 transport token 绑定
- 固定 `connection_epoch` fencing

最小交付：

- `pc_hello`
- `hello_ack`
- 服务端持久化 `pc` 记录
- 旧连接失效时拒绝继续写入

验收标准：

- 同一 `pc_id` 重连后，只有最新 `connection_epoch` 生效
- operator 能看到该节点当前是否 `online/stale/offline`

### 4.2 Slice B: `workspace_snapshot`

目标：

- 让 `VPS` 拿到可路由资源清单

最小交付：

- `workspace_snapshot`
- 服务端 `workspace` upsert
- 基于 `pc_id + workspace_id` 的最小查询能力

验收标准：

- 一台 PC 上报多个 workspace 后，服务端可稳定列出它们
- workspace 从属于单一 `pc_id`

### 4.3 Slice C: `execution_policy`

目标：

- 把 `backend/profile/permission` 提升为控制面一等字段

最小交付：

- `execution_policy`
- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- 可选 `backend_transport_modes`

固定语义：

- `backend`
  - 第一版固定为 `codex | opencode`
  - `new_task` 必须显式给出
  - follow-up 默认继承 session 当前 backend
- `profile`
  - 使用稳定 profile label，而不是 raw model id
  - 真实模型由绑定 PC 本地解析
- `permission`
  - 第一版固定为 `default | highest`
  - 新建 session 省略时表示 backend 默认权限
  - follow-up 省略时表示继承 session 当前权限

最小验收：

- `PC` 能上报自己支持的 backend/profile/permission 组合
- `VPS` 能在 dispatch 前校验目标 PC 是否支持该策略
- 若 profile 无法解析到本地模型，PC 能稳定拒绝而不是静默回退

### 4.4 Slice D: `command_dispatch -> command_ack`

目标：

- 打通控制面最小闭环

最小交付：

- `command_dispatch`
- `command_ack`
- `command_id` 幂等键
- `accepted / accepted_but_queued / rejected`
- `unsupported_backend / unsupported_profile / unsupported_permission / profile_model_unresolved`

验收标准：

- `VPS` 能对指定 `pc + workspace` 下发命令
- `PC` 能回 `command_ack`
- 重发同一 `command_id` 时不产生重复执行
- 不支持的 `execution_policy` 能被显式拒绝

### 4.5 Slice E: `event`

目标：

- 打通结构化状态流

最小交付：

- `event`
- 首批 `event_type`
  - `queued`
  - `accepted`
  - `running`
  - `awaiting_user_input`
  - `paused`
  - `done`
  - `failed`
  - `killed`

验收标准：

- `VPS` 可持久化并展示一条 session/run 的状态时间线
- 重放同一 `event_id` 不产生双写
- `running` 类事件可带回实际生效的执行策略摘要

### 4.6 Slice F: `result`

目标：

- 让命令真正收口

最小交付：

- `result`
- `final_status`
- `summary`
- 最小 `structured_payload`
- `effective_execution`

验收标准：

- 一个 `command_id` 最多只收敛到一个 canonical result
- `VPS` 可从 `result` 构建最小 session summary
- `result` 可回传：
  - `backend`
  - `profile`
  - `permission`
  - `backend_transport`
  - `resolved_model`

### 4.7 Slice G: `output_chunk`

目标：

- 把流式输出变成正式协议对象

最小交付：

- `output_chunk`
- `stream_id + seq` 去重与排序
- 基础 replay request

验收标准：

- `VPS` 可以按 `stream_id + seq` 展示连续输出
- 断线后能从缺失游标继续补发

### 4.8 Slice H: `artifact_manifest`

目标：

- 把 artifact 元数据纳入控制面

最小交付：

- `artifact_manifest`
- 最小 artifact metadata 持久化

验收标准：

- `VPS` 能知道某次 run 产生了哪些 artifact
- 文件实体仍可先留在 PC 或通过后续代理方式暴露

## 5. 推荐落地顺序

推荐按以下顺序推进：

1. Slice A
2. Slice B
3. Slice C
4. Slice D
5. Slice E
6. Slice F
7. Slice G
8. Slice H

原因：

- 先有节点身份，才能谈路由
- 先有 workspace 清单，才能谈命令归属
- 先把 `execution_policy` 冻结成一等对象，后续 command 才不会重走 mail 时代的散落字段
- 再有 command 和 event/result，工作台才有最小业务闭环
- 流式输出和 artifact 放在后面更稳，不会先把状态机做乱

结合当前 repo-side 进度，更准确的下一刀应读作：

- A-H 都已有 first-pass 骨架
- 下一次真正继续编码时，优先转向 `/v1/files` owner lane 的 cutover/decommission 观察窗口；如果还要继续扩 `pc-control` live 联调，应把更高层多 `PC` observer / subscription 侧需求单列。当前 live deployment 的 `22 MiB -> file_surface` 与 `34 MiB -> cos` 真实样本已经收齐，single-PC live `dispatch -> ack -> event -> result -> output_chunk`、live `output_resume_request` / selective replay、live `artifact_manifest`，以及 multi-PC live routing 也已收齐，不必再默认把 live `COS` compatibility evidence、single-PC roundtrip，或多 `PC` routing 当成主缺口。`OpenCode` true incremental streaming 可作为后续增强项单列，而不是继续扩旧 direct 兼容层
  - `/v1/files` / `COS` 的具体执行清单见 `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`

## 6. 与当前代码线的关系

Phase 1 推进时，当前代码线应这样处理：

- `docs/current/*` 继续描述今天已经存在的 mail-first 行为
- 旧 direct relay/control/file 文档继续保留为 compatibility / closeout / migration reference
- 不要求在 Phase 1 一开始就删除 mail、`session_action_closeout` 或旧 closeout bundle

更准确地说：

- 先新增新的控制面骨架
- 再尽快把新控制面推进到可直接切入 `VPS` 的 cutover 条件
- mail 线在这期间只做兼容与回归守护，不再新增长期 owner 语义
- `COS` 在这期间只作为 artifact external-delivery 的兼容线保留，不再新增长期 owner 语义；owner lane 应收敛到 `VPS /v1/files`
- cutover 条件满足后，按清单退场 mail-facing 结构，而不是把它们留成长期并存层
- `/v1/files` cutover 条件满足后，按清单退场 `COS` external-delivery 线，而不是把它留成长期并存层

### 6.1 `COS` 退场口径

后续 planning 对 `COS` 的正确处理应是“暂时兼容，条件满足后删除”，不是“长期双通道共存”。

建议按以下口径准备退场：

- 进入退场准备的条件：
  - target deployment 已把 `VPS /v1/files` 作为默认 artifact external-delivery lane
  - `artifact_manifest` / `external_delivery_index.json` 已足以承载 consumer 所需的 provider/url 证据，不再需要暴露 `COS`-specific contract
  - live `/v1/files` roundtrip、过期下载、以及当前目标文件规模所需的稳定性证据已经收齐
- 进入实际退场时的动作：
  - 停止把 live `COS` evidence 当成主线 merge/cutover gate
  - 停止在新的 planning / config 示例里把 `COS` 写成默认 external-delivery lane
  - 删除 `COS` routing/config/smoke/docs 中仅为兼容保留的 owner-lane 语义
- 只有在当前真实部署仍明确依赖 `COS` 时，才继续补 `COS` 相关 live evidence；否则优先把 `/v1/files` owner lane 收硬

## 7. 验收清单

- [x] `pc_hello / hello_ack` 可跑通（repo-side protocol/runtime/fixture）
- [x] `pc_id + connection_epoch` fencing 生效（repo-side store/runtime/fixture）
- [x] `workspace_snapshot` 可持久化（repo-side inventory store / client / fixture）
- [x] `backend/profile/permission` 的 `execution_policy` 可被稳定上报、校验和拒绝（repo-side protocol/runtime/tests）
- [x] `command_dispatch -> command_ack` 可跑通（最小 skeleton，已覆盖 `accepted` / `accepted_but_queued`）
- [x] `event` 可持久化并支持去重（repo-side runtime/store/tests/fixture）
- [x] `result` 可作为 session/run 最小收口（repo-side runtime/store/client/tests/fixture）
- [x] `result` 可回传实际生效的模型与权限摘要（`effective_execution` 已进入 protocol/runtime/client/result）
- [x] `output_chunk` 可按 `stream_id + seq` 连续显示（repo-side packet/runtime/store/client/tests、persisted-output reconnect resend、显式 `output_resume_request`、fixture loopback selective replay、websocket roundtrip，以及 `OpenCode SDK` same-layer persisted stream evidence 已落地；多 `PC` 更高层证据与 `OpenCode` true incremental streaming 增强验证仍待补）
- [x] `artifact_manifest` 可回填最小 artifact metadata（first-pass packet/runtime/store/client/tests 已落地，并已补到真实 `artifact_index.json + artifact_file_binding_index.json` 本地 truth-projection、`external_delivery_index.json`、live local relay `/v1/files` external-delivery evidence、真实 VPS relay `/v1/files` upload + metadata/content evidence，以及 live deployment 下 `22 MiB -> file_surface` / `34 MiB -> cos` 的真实业务样本；`COS` 当前只按临时兼容 lane 读取）
- [x] single-PC live `command_dispatch -> command_ack -> event -> result -> output_chunk` 已在真实 VPS relay 上留证（其中显式 `profile=default` 语义也已在修复后重新验证通过）
- [x] single-PC live `output_resume_request` / selective replay 与 live `artifact_manifest` 已在真实 VPS relay 上留证
- [x] multi-PC live routing 已在真实 VPS relay 上留证（双 probe `pc_id` 并发在线时，定向 dispatch 已可稳定只命中目标连接，不再串投到另一条 websocket）
- [ ] 不破坏当前 `docs/current/*` 所描述的 mail-first 现状

## 8. 一句话结论

**Phase 1 的任务不是“完成新平台”，而是先把 `PC 节点 -> workspace 清单 -> command -> event/result -> output_chunk` 这条最小控制面骨架稳稳立起来。**
