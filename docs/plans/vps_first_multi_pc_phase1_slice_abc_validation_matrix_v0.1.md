# VPS-First 多 PC Phase 1 Slice A-C 验证矩阵（v0.1）

## Status

- Date: 2026-03-25
- Scope: `VPS-first 多 PC 控制面` 主线下，repo-side `Slice A-C` 的验证矩阵
- Source of truth:
  - `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md`
  - `docs/plans/vps_first_multi_pc_phase1_slice_abc_implementation_design_v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`

## 1. 目标

本文不重复字段定义，只回答三件事：

- Slice A-C 各自要验什么
- 证据应该落在哪一层测试
- 哪些场景必须在 merge 前拿到

## 2. 总体验证策略

第一阶段推荐按四层验证：

1. 协议单测
2. store 单测
3. runtime / handler 集成测试
4. 最小主机联调或 VPS smoke

当前不建议一开始就把大量 Android 联调绑进 Slice A-C merge gate，因为这三刀的主目标是先把 repo-side 节点模型和 inventory 模型立稳。

## 3. Slice A: `pc_hello / hello_ack / heartbeat / connection_epoch`

### 3.1 协议单测

建议测试：

- `tests/test_relay_server_pc_control_protocol.py`

最少覆盖：

- `pc_hello` 缺少 `pc_id`、`display_name`、`sent_at` 时拒绝
- `hello_ack` 必带 `connection_epoch`
- `heartbeat` 必带 `pc_id` 与 `connection_epoch`
- 旧 epoch frame 被 parser/runtime 明确拒绝

### 3.2 store 单测

建议测试：

- `tests/test_relay_server_pc_node_store.py`
- `tests/test_relay_server_pc_credential_registry.py`

最少覆盖：

- 同一 `pc_id` 首次 hello 生成 `connection_epoch = 1`
- 同一 `pc_id` 再次 hello 生成更大的 epoch
- 旧 epoch heartbeat 不更新 `last_seen_at`
- credential disabled 时拒绝建立连接

### 3.3 runtime / handler 集成

建议测试：

- `tests/test_relay_server_pc_control_runtime.py`

最少覆盖：

- Bearer token -> `auth_credential_id -> pc_id` 解析成功
- hello 后返回 `hello_ack`
- 同一 `pc_id` 第二连接建立后，第一连接继续 heartbeat 被拒绝
- `status` 从 `online` 到 `stale` 的投影更新

### 3.4 手工 smoke

最少证据：

- 一台 PC 连到 VPS 后，operator 能在 health/debug 视角看到：
  - `pc_id`
  - `display_name`
  - `connection_epoch`
  - `last_seen_at`

## 4. Slice B: `workspace_snapshot`

### 4.1 协议单测

建议测试：

- `tests/test_relay_server_pc_control_protocol.py`

最少覆盖：

- `workspace_snapshot` 缺少 `workspaces[]` 时拒绝
- `workspace` entry 缺少 `workspace_id` / `repo_path` 时拒绝
- `workspace_norm` 与 `display_name` 可选但形状必须正确

### 4.2 store 单测

建议测试：

- `tests/test_relay_server_workspace_inventory_store.py`

最少覆盖：

- 同一 `pc_id::workspace_id` 重复上报时表现为幂等更新
- 不同 `pc_id` 下相同 `workspace_id` 可以并存
- `repo_path` / `workdir` 不一致时触发显式错误或拒绝策略，而不是静默覆盖

### 4.3 runtime / handler 集成

建议测试：

- `tests/test_relay_server_pc_control_runtime.py`
- `tests/test_pc_control_plane_client.py`

最少覆盖：

- PC 侧能基于 `project_sync_roots` 生成 snapshot
- VPS 侧能稳定 upsert 多个 workspace
- 旧 epoch 的 snapshot 被拒绝
- snapshot 后服务端可按 `pc_id` 列出 workspace

### 4.4 手工 smoke

最少证据：

- 两台 PC 同时在线时，VPS 能看到两套 workspace 清单
- 若两台 PC 恰好存在同名或同路径显示项，服务端不会发生覆盖

## 5. Slice C: `execution_policy`

### 5.1 协议单测

建议测试：

- `tests/test_relay_server_pc_control_protocol.py`

最少覆盖：

- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- `backend_transport_modes`

以上字段的缺省、空值、非法值都应有明确校验结果。

### 5.2 store 单测

建议测试：

- `tests/test_relay_server_pc_node_store.py`

最少覆盖：

- hello/heartbeat 中能力快照可被稳定写入节点记录
- 能力快照更新时不会清空无关字段
- 不支持的 backend/profile/permission 不会被静默规范化成默认值

### 5.3 runtime / handler 集成

建议测试：

- `tests/test_relay_server_pc_control_runtime.py`

最少覆盖：

- PC 上报 `codex | opencode` 组合后，VPS 侧可读出完整 policy 摘要
- profile catalog 中声明了稳定 label，但本地无映射时，后续 dispatch 前能显式拒绝
- `permission = highest` 未被该 PC 声明支持时，后续 admission 明确失败

### 5.4 手工 smoke

最少证据：

- operator 能看到每台 PC 最近一次上报的 `backend/profile/permission` 能力
- 不同 PC 能有不同 profile catalog，不发生相互污染

## 6. 建议的测试文件落点

repo-side 当前已落地并应继续扩展：

- `tests/test_relay_server_pc_control_protocol.py`
- `tests/test_relay_server_pc_credential_registry.py`
- `tests/test_relay_server_pc_node_store.py`
- `tests/test_relay_server_workspace_inventory_store.py`
- `tests/test_relay_server_pc_control_runtime.py`
- `tests/test_pc_control_plane_client.py`

未来如果把 host 生命周期和自动注册真正接到常驻运行线，再考虑新增：

- `tests/test_host_pc_control_registration.py`

## 7. Merge Gate

截至 `2026-03-25`，仓库内已经拿到以下 repo-side 机器可读证据：

- `pc_hello / hello_ack` 单测通过
- `connection_epoch` fencing 单测通过
- `workspace_snapshot` upsert 单测通过
- `execution_policy` 能力快照单测通过
- 至少一条 runtime 集成测试覆盖 `hello -> heartbeat -> workspace_snapshot`

这组证据当前的作用不再是“是否允许开始进入 Slice D”，而是：

- 作为 Slice A-F 已落地后的基础回归门，并在继续推进 Slice G/H 前持续保留
- 防止后续重构把 A-C 已落地骨架打回“只有计划没有实现”的状态

如果这些证据在后续改动中失效，不建议继续推进 canonical `output_chunk / artifact_manifest` 实现。

## 8. 暂不要求的证据

在 Slice A-C merge gate 之前，以下证据不是必须项：

- Android 端到端 UI 自动化
- `command / event / result` 全闭环
- `output_chunk` replay
- `artifact_manifest`
- mail backup/export 验证

这些内容应放到后续切片中验。

## 9. 一句话结论

**Slice A-C 的验证重点不是“消息能发出去”，而是“节点身份、连接代次、workspace inventory、execution policy 能不能被稳定持久化、稳定拒绝、稳定重连”。**
