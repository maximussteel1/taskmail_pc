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

换句话说，Phase 1 要先把“控制面骨架”立住，而不是先把 mail 完全踢掉。

## 2. 非目标

Phase 1 明确不做：

- 不做跨 PC 热迁移
- 不做共享 workspace 的多 PC 执行
- 不做 repo/worktree/native session 迁到 VPS
- 不做完整多用户 ACL
- 不做 mail 全面删除
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

## 6. 与当前代码线的关系

Phase 1 推进时，当前代码线应这样处理：

- `docs/current/*` 继续描述今天已经存在的 mail-first 行为
- 旧 direct relay/control/file 文档继续保留为 compatibility / closeout / migration reference
- 不要求在 Phase 1 一开始就删除 mail、`session_action_closeout` 或旧 closeout bundle

更准确地说：

- 先新增新的控制面骨架
- 再逐步削弱旧兼容层的产品主地位
- 最后再决定哪些 mail-facing 结构可以退场

## 7. 验收清单

- [ ] `pc_hello / hello_ack` 可跑通
- [ ] `pc_id + connection_epoch` fencing 生效
- [ ] `workspace_snapshot` 可持久化
- [ ] `backend/profile/permission` 的 `execution_policy` 可被稳定上报、校验和拒绝
- [ ] `command_dispatch -> command_ack` 可跑通
- [ ] `event` 可持久化并支持去重
- [ ] `result` 可作为 session/run 最小收口
- [ ] `result` 可回传实际生效的模型与权限摘要
- [ ] `output_chunk` 可按 `stream_id + seq` 连续显示
- [ ] `artifact_manifest` 可回填最小 artifact metadata
- [ ] 不破坏当前 `docs/current/*` 所描述的 mail-first 现状

## 8. 一句话结论

**Phase 1 的任务不是“完成新平台”，而是先把 `PC 节点 -> workspace 清单 -> command -> event/result -> output_chunk` 这条最小控制面骨架稳稳立起来。**
