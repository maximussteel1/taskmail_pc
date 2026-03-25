# VPS-First 多 PC Phase 1 Slice A-C 实现设计（v0.1）

## Status

- Date: 2026-03-25
- Scope: `VPS-first 多 PC 控制面` 主线下，repo-side `Slice A-C` 的实现层设计
- Covers:
  - Slice A: `pc_hello / hello_ack / heartbeat / connection_epoch`
  - Slice B: `workspace_snapshot`
  - Slice C: `execution_policy`
- Source of truth:
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-vps-first-control-plane-freeze-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`

## 1. 目标与边界

本文回答的不是“字段长什么样”，而是“PC 仓第一刀代码应该落在哪里、复用什么、不要误复用什么”。

本文只覆盖：

- `PC` 节点身份、认证、`connection_epoch` fencing
- `workspace_snapshot` 的服务端落库与客户端采集
- `execution_policy` 的服务端可见性与 admission 前校验基础

本文明确不覆盖：

- `command / event / result / output_chunk / artifact_manifest` 的完整实现
- Android UI 或 Android 侧 DTO 组织
- mail-first current behavior 的删除
- 多用户 ACL 与 operator 后台

## 2. 设计原则

### 2.1 复用现有 relay hosting，不复用旧语义边界

第一阶段应复用：

- `mail_runner/relay_server/app.py` 的 HTTP/WebSocket server 外壳
- `mail_runner/relay_server/auth.py` 的 Bearer token admission 模式
- `mail_runner/relay_server/packet_store.py` 的 JSON 持久化风格

但不应继续把当前 Android-facing `/control` 语义直接扩展成多 PC 控制面真相层。

### 2.2 `workspace` 仍然是 `pc-scoped`

`workspace_id` 可以继续沿用 `repo_path + workdir` 的稳定派生方式，但在 VPS 侧不能把 `workspace_id` 单独当成全局主键。

第一阶段服务端正确主键应为：

- `pc_id + workspace_id`

换句话说：

- `workspace_id` 用于稳定命名
- `pc_id + workspace_id` 用于路由和持久化唯一性

### 2.3 `display_name` 只做展示，真正绑定靠稳定 id

- `pc.display_name` 允许修改
- `pc_id` 不允许因重装、改名、换目录而漂移
- `online / stale / offline` 是投影状态，不直接手写布尔值

### 2.4 先把控制面骨架立住，再谈切流

当前 relay 连通性已经不是主 blocker。Phase 1 的任务应从“能不能通”转成“能不能以稳定 store 和稳定 handler 形状长期演进”。

## 3. 现有代码资产的正确读法

### 3.1 可以直接复用的资产

| 现有文件 | 第一阶段建议角色 | 备注 |
| --- | --- | --- |
| `mail_runner/relay_server/app.py` | 复用 server hosting、HTTP health、WebSocket upgrade | 适合承载新的 PC ingress path |
| `mail_runner/relay_server/auth.py` | 复用 token 指纹与 Bearer 校验模式 | 但要从“单 token”演进到“per-PC credential” |
| `mail_runner/relay_server/packet_store.py` | 复用持久化风格与 JSON store 写法 | 适合作为 `pc_node_store` / `workspace_store` 的代码风格模板 |
| `mail_runner/thread_store.py` | 复用 `build_workspace_id()` / `build_workspace_norm()` | 这是当前 repo 内最稳定的 workspace identity helper |
| `mail_runner/project_folder_sync.py` | 复用根目录枚举逻辑 | 可作为 `workspace_snapshot` 第一版采集来源 |
| `mail_runner/config.py` | 复用 `project_sync_roots` 与 profile-model 映射配置 | 第一阶段不需要重新发明一套 roots 配置 |
| `mail_runner/relay_server/config.py` | 复用 relay server config 入口 | 但需要扩出 PC credential registry 配置 |

### 3.2 不应误复用的资产

| 现有文件 | 不应承担的新责任 | 原因 |
| --- | --- | --- |
| `mail_runner/relay_server/session_store.py` | 不应变成 canonical `pc` / `workspace` store | 它当前服务的是 relay connection/subscription runtime，不是多 PC 控制面资源模型 |
| `mail_runner/relay_server/control_protocol.py` | 不应直接膨胀成新 PC 协议 authority | 它当前是旧 Android `/control` compatibility helper，语义和主线已分叉 |
| `mail_runner/relay_server/direct_actions.py` / `post_creation_actions.py` | 不应承担 Slice A-C 的节点模型 | 它们属于旧 direct action seam |

## 4. 推荐模块拆分

### 4.1 VPS 侧新增模块

建议新增以下模块，而不是把所有逻辑继续塞进 `app.py`：

- `mail_runner/relay_server/pc_control_protocol.py`
  - 职责：解析/构建 `pc_hello`、`hello_ack`、`heartbeat`、`workspace_snapshot`
  - 目标：和旧 `control_protocol.py` 分离，避免 Android 兼容层继续污染主线
- `mail_runner/relay_server/pc_node_store.py`
  - 职责：持久化 `pc_id`、`display_name`、`auth_credential_id`、`connection_epoch`、`last_seen_at`
- `mail_runner/relay_server/workspace_inventory_store.py`
  - 职责：持久化 `pc_id + workspace_id` 维度的 workspace 清单
- `mail_runner/relay_server/pc_credential_registry.py`
  - 职责：管理 `auth_credential_id -> pc_id -> token hash`
- `mail_runner/relay_server/pc_control_handlers.py`
  - 职责：把协议消息翻译成 store 操作
- `mail_runner/relay_server/pc_control_runtime.py`
  - 职责：协调 protocol、credential registry、node store、workspace store，并对 `app.py` 暴露最小 handler

### 4.2 PC 侧新增模块

- `mail_runner/pc_control_plane_client.py`
  - 职责：维护与 VPS 的长连接、发送 `pc_hello`、定期 heartbeat、发送 `workspace_snapshot`
- `mail_runner/pc_workspace_inventory.py`
  - 职责：收敛 workspace 采集逻辑，避免 `host.py` 里散落目录扫描细节

### 4.3 应修改而非重写的模块

- `mail_runner/relay_server/app.py`
  - 增加新的 PC ingress path 与 handler dispatch
- `mail_runner/relay_server/config.py`
  - 增加 PC credential registry 路径或配置来源
- `mail_runner/host.py`
  - 承接 PC control plane client 的生命周期

## 5. ingress 与 handler 边界

### 5.1 推荐新增独立 PC ingress

第一阶段推荐新增独立 WebSocket ingress，例如：

- `/pc-control`

理由：

- 避免把当前 Android `/control` compatibility helper 继续当成未来主协议入口
- 让 `pc_hello`、`workspace_snapshot`、`heartbeat` 不必迁就旧 direct contract 命名
- 便于在 `app.py` 中把“PC worker 接入”和“Android/legacy direct 接入”分成两套 handler

如果实施时暂时复用 `/control` path，也应满足：

- 协议 parser 独立
- store 独立
- handler 入口独立

不能只是在旧 `control_protocol.py` 里继续追加分支。

### 5.2 `app.py` 中的推荐分层

`app.py` 最终只做三件事：

- 路径分发
- 基础 admission
- 调用 runtime handler

不建议让 `app.py` 直接承担：

- `connection_epoch` 递增逻辑
- credential registry 读写
- workspace upsert 细节
- execution policy 校验细节

## 6. 持久化模型

### 6.1 推荐目录

建议在 relay `state_dir` 下新增独立目录：

- `state_dir/pc_control/`

第一阶段建议最少包含：

- `pc_credentials.json`
- `pc_nodes.json`
- `workspaces.json`

### 6.2 `pc_credentials.json`

用途：

- operator 预注册每台 PC 的 credential
- 服务端在 WebSocket upgrade 后解析 Bearer token，并映射到 `pc_id`

建议字段：

- `auth_credential_id`
- `pc_id`
- `display_name`
- `token_sha256`
- `enabled`
- `created_at`
- `updated_at`

注意：

- 不建议把 raw token 再写回 state store
- state store 中保存 hash 或指纹即可

### 6.3 `pc_nodes.json`

用途：

- 持有节点当前的 canonical 连接状态与最近能力快照

建议字段：

- `pc_id`
- `display_name`
- `auth_credential_id`
- `current_connection_id`
- `current_connection_epoch`
- `connected_at`
- `last_seen_at`
- `status`
- `client_version`
- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- `backend_transport_modes`
- `updated_at`

注意：

- `status` 仍是投影值，但持久化缓存它有利于 operator/debug
- 旧连接只要 `connection_epoch` 落后，就必须被拒绝写入

### 6.4 `workspaces.json`

用途：

- 让 VPS 拿到“某台 PC 当前能路由到哪些目录”

建议字段：

- `pc_id`
- `workspace_id`
- `workspace_norm`
- `repo_path`
- `workdir`
- `display_name`
- `source`
- `updated_at`

推荐键：

- 内存键和存储唯一键都使用 `pc_id::workspace_id`

这样可以避免：

- 两台 PC 恰好有同一路径时互相覆盖
- 后续一旦引入 project/group 层时需要重做主键

## 7. Slice A 详细设计

### 7.1 服务端 admission 顺序

1. WebSocket upgrade 到 `/pc-control`
2. 解析 Bearer token
3. 通过 `pc_credential_registry` 找到 `pc_id + auth_credential_id`
4. 解析 `pc_hello`
5. 在 `pc_node_store` 中递增并保留新的 `connection_epoch`
6. 返回 `hello_ack`
7. 后续所有 heartbeat / snapshot 写入都要求携带最新 `connection_epoch`

### 7.2 `connection_epoch` 的 repo-side 读法

正确语义：

- 同一 `pc_id` 每次新连接成功 hello，都生成新的 `connection_epoch`
- 较旧连接即使还活着，也失去写权限
- 服务端只接受“最新 epoch”的 heartbeat 和 snapshot

这层 fencing 必须落在 store/runtime 层，而不是只靠 WebSocket 对象内存态判断。

### 7.3 `heartbeat`

第一阶段 heartbeat 的责任很窄：

- 更新 `last_seen_at`
- 维持 `status` 投影
- 可回写最小运行态统计，例如 `active_run_count`

第一阶段不要求 heartbeat 承担：

- command 流 replay
- session 级别 health diagnosis

## 8. Slice B 详细设计

### 8.1 workspace 采集来源

第一阶段建议优先复用：

- `project_sync_roots`
- `project_folder_sync.list_project_folders()`

把它们作为“可投递目录清单”的第一版来源。

如果同一台 PC 后续还要补充“已有 session 索引中的 workspace”，可在第二刀引入，但不应阻塞第一阶段最小 inventory。

### 8.2 `workspace_id` 生成

第一阶段继续复用：

- `thread_store.build_workspace_id(repo_path, workdir)`
- `thread_store.build_workspace_norm(repo_path, workdir)`

原因：

- 当前 repo 已经用这套逻辑承载 session/workspace 索引
- 现在改 workspace id 算法，会制造不必要的历史割裂

### 8.3 workspace upsert 规则

- 同一 `pc_id::workspace_id` 重复上报时，应表现为幂等更新
- 同一 `workspace_id` 在不同 `pc_id` 下必须并存
- `display_name` 可随 snapshot 更新
- `repo_path` / `workdir` 变化应视为同一 `workspace_id` 的异常，不应静默覆盖

## 9. Slice C 详细设计

### 9.1 `execution_policy` 的采集范围

第一阶段建议放在 node 级别，不放在 workspace 级别：

- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- `backend_transport_modes`

原因：

- 当前 `codex/opencode` 能力主要依赖 PC 本机环境，而不是某个 workspace 目录
- 先把 node 级能力稳定暴露出来，足够支撑 dispatch admission

### 9.2 profile 解析责任

冻结读法：

- `profile` 是稳定标签，不是 raw model id
- raw model 解析发生在 PC 本地
- PC 必须把实际解析结果回报为 `resolved_model`

因此 Slice C 第一阶段应至少做到：

- PC hello 时上报支持的 profile catalog
- 服务端保留最近一次 catalog 快照
- 后续 dispatch 前可检查“目标 PC 是否声明支持该 profile”

### 9.3 权限读法

第一阶段权限只做两级：

- `default`
- `highest`

repo-side 需要做的是：

- 能看到目标 PC 声明支持哪些 permission mode
- 在后续 command admission 时拒绝不支持的 mode

repo-side 第一阶段不需要知道 Codex/OpenCode 每个底层开关的全部细节；那部分已经冻结在跨仓 `execution_policy` 附录里。

## 10. 推荐代码落地顺序

### 10.1 先立 store，再立 handler

推荐顺序：

1. `pc_credential_registry`
2. `pc_node_store`
3. `workspace_inventory_store`
4. `pc_control_protocol`
5. `pc_control_runtime`
6. `app.py` path dispatch
7. `pc_control_plane_client`
8. `host.py` lifecycle 接入

原因：

- 先有稳定 store，才能把 fencing 和幂等写清楚
- 先有 protocol builder/parser，handler 才不会反复改消息形状

### 10.2 不建议的顺序

不建议先做：

- 直接往 `app.py` 堆 if/else
- 先写 client reconnect，再补 store
- 先写 command/event/result，再补 `pc_hello`

这样会把第一阶段最重要的主键、连接代次和 inventory 模型做乱。

## 11. 验证入口

本文只定义实现落点，不展开具体验收矩阵。

实现与联调应继续参考：

- `docs/plans/vps_first_multi_pc_phase1_slice_abc_validation_matrix_v0.1.md`

## 12. 一句话结论

**Slice A-C 的 repo-side 正确做法是：沿现有 relay hosting 和 JSON store 风格演进，但把 `pc`、`workspace inventory`、`execution_policy` 明确拆成新的 runtime/store/protocol 模块，而不是继续把旧 Android `/control` compatibility layer 硬撑成未来主控制面。**
