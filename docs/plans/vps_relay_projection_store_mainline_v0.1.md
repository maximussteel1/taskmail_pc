# VPS Relay 原生 Projection Store 主线方案（v0.1）

## 状态

- 日期：2026-03-29
- 范围：把 Android 当前读面从 `shared task_root` 镜像依赖，收敛到 relay-native projection store
- 层级：repository-side active mainline execution plan
- 当前执行状态：
  - `2026-03-29` 已直接完成 Android `sessions / session-snapshot / session-history / session-updates` 对 relay-native projection store 的主链切换
  - 代码层不再保留 `task_root/shadow` Android read-source 分支，也不再保留 shadow-compare 配置开关
  - `sync_relay_task_root.py` 已退出 Android 在线 read path 主链，仅保留为迁移 / 取证工具
- 相关文档：
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `docs/plans/vps_relay_projection_store_schema_v0.1.md`
  - `docs/plans/vps_relay_projection_publisher_protocol_v0.1.md`
  - `docs/plans/vps_relay_projection_cutover_shadow_compare_v0.1.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/android_sessions_facade_contract.md`
  - `docs/current/android_session_snapshot_facade_contract.md`
  - `docs/current/android_session_history_rounds_contract.md`
  - `docs/current/android_session_updates_facade_contract.md`
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/README.md`

## 1. 一句话决策

`shared task_root sync` 不再应被读成当前主线的长期 owner seam。

当前主线应明确切到：

- `PC` 保留本地 `task_root` 作为 execution / evidence truth
- `relay` 维护自己的 Android-facing projection store
- Android 继续读现有 `/v1/android/*` contract，不要求首轮改动
- `sync_relay_task_root.py` 逐步退出在线主链，降级为 migration / operator-only 工具

## 2. 为什么这件事现在必须收口

当前 `sync companion` 的语义不是“增量同步几个状态文件”，而是：

1. 遍历整棵本地 `task_root`
2. 计算整树 fingerprint
3. 一旦任意文件变化，就重新打包整棵目录
4. 上传到 VPS
5. 在远端整棵替换 relay-visible `task_root`

这条路径在 bring-up 阶段可接受，但它不适合作为长期主线，原因不是单点实现粗糙，而是架构方向错误：

- relay 在线读面依赖整棵 PC 文件树影子，而不是依赖 relay 自己的控制面存储
- Android detail/history 新鲜度依赖“PC 文件树同步是否成功”，而不是依赖控制面对象是否已到达 relay
- `task_root` 历史越积越大，压缩、上传、解包、替换成本都会单调上升
- relay 对 PC 本地目录布局、保留策略和历史体量产生了不必要的强耦合
- companion 健康度变成 Android 当前态可读性的前置条件，这不应继续保留

这已经不是单纯的运维成本问题，而是当前主线向 `VPS-first` 收敛时的结构性阻塞。

## 3. 本方案要保留的边界

本方案是主线收敛，不是协议重写。首轮必须保留以下边界：

- Android app-facing contract 保持不变：
  - `POST /v1/android/create-session`
  - `POST /v1/android/session-action`
  - `GET /v1/android/sessions`
  - `GET /v1/android/session-snapshot`
  - `GET /v1/android/session-history`
  - `WS /v1/android/session-updates`
- Android 首轮不要求改代码，不要求改 payload shape，不要求改恢复语义
- `PC` 继续是 task execution truth；repo/worktree/native session 不迁到 VPS
- 本地 `task_root` 持久化格式不在本方案首轮重写
- `RunArtifact + artifact_index.json` 继续是 repo-local artifact truth
- `/v1/files` 继续是 artifact external-delivery owner lane
- `pc_control` command ledger 继续承担 current-session continuity
- mail protocol、reply-routing、question capsule、reporter 结构边界不因本方案隐式改写

## 4. 目标架构

### 4.1 真相分层

收敛后的长期分层应读作：

- **PC 本地真相**
  - `task_root`
  - `thread_state / session_state`
  - run outputs
  - raw mail evidence
  - closeout evidence
  - 本地 artifacts
- **relay 控制面真相**
  - `pc / workspace / session / command / event / result / artifact-ref / closeout-anchor` 的可路由投影
  - Android-facing session list/detail/history/update 所需 projection
  - operator read-side 所需可观测对象
- **mail 用户可见真相**
  - 当前 receipt / history / attachment 语义继续保留在现有边界

重点不是把 `PC` execution truth 迁到 VPS，而是停止让 relay 在线依赖一份“PC 文件树影子”。

### 4.2 在线数据流

目标在线链路应是：

1. `PC` 本地执行产生状态变化
2. `PC` 通过现有 `pc-control` 长连接发送 projection update
3. relay 把 update 做成 durable upsert
4. Android 读 relay projection store
5. `session-updates` 由 relay store change 驱动，而不是靠轮询镜像 `task_root` 重建 snapshot

这意味着：

- Android 当前态新鲜度取决于 control-plane update 是否到达 relay
- 不再取决于 tar 包有没有完成下一次整树替换

### 4.3 首轮不做什么

本方案当前明确不做：

- 不优化整包同步脚本后继续长期保留它
- 不让 relay 变成 `task_root` 主库
- 不要求 Android 切到新 endpoint
- 不把 repo-local artifact truth 迁成 relay 文件主键
- 不引入“VPS 直接读 PC 本地路径”
- 不把多 PC 共享 workspace、热迁移 running session 塞进同一轮

## 5. Relay 需要持有的 projection 对象

relay 侧不需要镜像整棵 `task_root`，但需要持有以下最小对象集。

### 5.1 `workspace inventory`

用途：

- 继续回答 `pc -> workspace` 路由问题
- 继续支撑 Android 环境/工作区读面

来源：

- 现有 `workspace_snapshot`

读法：

- 这是已落地主线的一部分，应继续复用，不需要回退到 `task_root` 扫描

### 5.2 `session head`

用途：

- 服务 `GET /v1/android/sessions`
- 服务 `GET /v1/android/session-snapshot` 的 summary/detail 头部字段
- 驱动 `WS /v1/android/session-updates` 的当前态变化

最小字段应覆盖当前 facade 已暴露的稳定语义：

- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`
- `session_name`
- `backend`
- `backend_transport`
- `profile`
- `permission`
- `repo_path`
- `workdir`
- `list_status`
- `snapshot_status`
- `lifecycle`
- `current_task_id`
- `queued_task_id`
- `pending_task_count`
- `last_summary`
- `last_active_at`
- `last_progress_at`
- `backend_session_id`
- `backend_session_resumable`
- `question_state`
- `timeline_items`
- `projection_version`
- `updated_at`

这里的关键是：

- relay 存的是 Android-facing projection，不是 `thread_state.json` 原文镜像
- `projection_version` 是 session 级单调版本号，不再依赖目录 mtime 近似“变化”

### 5.3 `session history rounds`

用途：

- 服务 `GET /v1/android/session-history`
- 为 `session_snapshot.history_rounds` 提供 durable truth

最小字段应覆盖当前 round projector 业务语义：

- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`
- `task_id`
- `round_id`
- `round_number`
- `created_at`
- `status`
- `speaker_label`
- `input`
- `process`
- `result`
- `projection_version`

这层不应再要求 relay 持有 `runs/<task_id>/result.json` 的远端文件影子后再现算。

### 5.4 `artifact refs`

用途：

- 为 history rounds / detail 提供稳定附件元数据
- 把 `artifact_id` 对到 `/v1/files` 的 `file_id / download_ref`

最小字段：

- `artifact_id`
- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`
- `task_id`
- `display_name`
- `content_type`
- `size_bytes`
- `is_image`
- `file_id`
- `download_ref`
- `download_ref_source`
- `provider`
- `expires_at`

这层的职责是 transport-facing file reference，不替代 PC 本地 `artifact_index.json`。

### 5.5 `closeout anchors`

用途：

- 保留 Android / operator / replay 所需的 canonical outcome anchors
- 替代 relay 对 `canonical_summary.json` / `session_action_closeout.json` 影子文件的在线依赖

最小字段：

- `request_id`
- `packet_id`
- `receipt_id`
- `action_type`
- `target_session_identity`
- `last_summary`
- `terminal_mail_message_id`
- `terminal_mail_subject`
- `generated_at`

### 5.6 `transport probe observations`

用途：

- 服务 relay-side `transport_probe` 对账
- 不再依赖 `_mailbox/transport_probes/*.json` 的远端镜像

## 6. PC 侧需要新增的职责

长期最优方向不是让 relay 去“拉目录”，而是让 `PC` 去“推对象”。

### 6.1 复用现有通道

首选做法：

- 继续复用现有 `pc-control` 长连接
- 不另开第二条专门的 task-root-sync 业务链路

现有已落地并可复用的基础包括：

- `workspace_snapshot`
- `command_dispatch -> command_ack -> event -> result`
- `artifact_manifest`
- `/v1/files`

### 6.2 新增 projection message family

repo-side 当前主线应新增一组窄范围 projection message，而不是同步文件树。

首轮建议最小 family：

1. `session_projection_upsert`
   - 写当前 session head
2. `session_round_upsert`
   - 写/更新一轮 history round
3. `session_closeout_upsert`
   - 写 direct action / terminal outcome anchor
4. `transport_probe_observation_upsert`
   - 写 probe observation

如需解决重连后的全量恢复，再补：

5. `session_projection_snapshot`
   - `PC` 向 relay 重发自己当前持有的 active / recent session 投影

### 6.3 触发点

PC 侧最少要在这些时机发 projection update：

- create-session accepted / queued
- session running
- awaiting_user_input
- paused / resumed
- end / kill / terminal done/failed
- run finished 后 history round 成形
- artifact upload 完成并拿到 `/v1/files` 引用
- closeout anchor 成形
- probe observation 成形

这套触发点必须直接挂在状态变化点上，而不是挂在“某个目录下又多了一个文件”上。

### 6.4 幂等与排序

所有 projection message 必须具备：

- 稳定主键
- 幂等 upsert 语义
- session 内单调 `projection_version`
- 明确的 source timestamp

relay 不应再通过目录扫描顺序、mtime 或 tar 替换时机推断业务先后。

## 7. Relay 侧需要新增的职责

### 7.1 新的 durable store

当前主线建议直接在 relay 上落一个独立的 projection store。

首轮推荐：

- `SQLite`

原因：

- 单机 VPS 场景足够
- 比继续堆 JSON sidecar 更适合幂等 upsert、索引和查询
- 不会像整树镜像那样把保留策略与热路径绑死

### 7.2 Android facade 改读 store

以下读面都应切到 projection store：

- `GET /v1/android/sessions`
- `GET /v1/android/session-snapshot`
- `GET /v1/android/session-history`
- `WS /v1/android/session-updates`

其中：

- `latest_session_action` 继续来自现有 `pc_command_store`
- `session` / `session_snapshot` / `history_rounds` 来自 projection store

### 7.3 `session-updates` 改成 store-driven push

长期目标是：

- 不再轮询 `task_root` 重建 snapshot
- 改成监听 projection version 变化并推送

这条变化是本方案收益的核心之一，因为它把 Android detail 实时性从“文件镜像轮询”切到“控制面对象变化”。

## 8. Android contract 兼容策略

本方案当前的关键约束是：

- **不改 Android contract**

因此当前迁移要求是：

- endpoint 不变
- locator 规则不变
- payload shape 不变
- error code 不变
- 冷启动、重连、补偿语义不变

也就是说：

- Android 首轮不需要改代码
- Android 不需要知道 relay 底层真相层已经从 `task_root` 镜像切到 projection store

若后续 Android 想利用更细粒度增量、离线缓存或订阅能力，那属于下一阶段增强，不是本方案首轮前提。

## 9. 迁移阶段

### 阶段 A：定性与定边界

目标：

- 把 `shared task_root sync` 明确降级为 migration scaffold
- 冻结 projection object model 和 PC/relay 分责

退出条件：

- 规划层 owner note 与索引都改成以 projection store 为当前主线

### 阶段 B：新增 relay projection store 与 PC 双写

目标：

- relay 能接收 projection upsert
- PC 在现有状态变化点上双写：
  - 继续写本地 `task_root`
  - 同时推 projection message 到 relay

退出条件：

- 不影响 current behavior
- projection store 已能覆盖 representative active session / terminal run / waiting-user session 样本

### 阶段 C：shadow compare

目标：

- relay 同时保留：
  - 现有 task-root-based read path
  - 新 projection-store-based read path
- 对同一 session 生成双份 payload 做对账

退出条件：

- representative 样本下：
  - sessions
  - session-snapshot
  - session-history
  - session-updates
  的关键字段对齐到可接受范围

### 阶段 D：切 Android facade 读源

目标：

- Android 现有 `/v1/android/*` 全切到 projection store
- `session-updates` 切到 store-driven push

退出条件：

- 关闭 companion 后，Android 当前态与历史仍可正常读取
- 现有 current-session action continuity 不回退

### 阶段 E：退场 shared task_root sync 在线依赖

目标：

- relay 不再依赖 remote `shared/task_root` 回答 Android 主链请求
- `sync_relay_task_root.py` 退出 service-management 主链

可保留的残余角色：

- 手动取证
- 故障导出
- 一次性 migration snapshot

但它不再是 Android live read path 的前置条件。

## 10. 验收标准

本方案完成后，至少应满足：

1. 停掉 `sync companion` 后，Android 的 `sessions / session-snapshot / session-history / session-updates` 仍能工作。
2. relay 在线热路径里不再扫描或依赖 remote `task_root`。
3. Android contract 无需改动即可消费新实现。
4. `latest_session_action` continuity 继续可读，且不因为读源切换而降级。
5. artifact 下载继续经 `/v1/files`，不暴露 PC 本地路径。
6. representative session 样本下，projection-store payload 与当前 task-root-based payload 关键业务字段一致。
7. `manage_mail_runner.ps1 status` 不再把 “Relay task-root sync companion 是否存活” 当成 Android 主链健康前提。

## 11. 当前建议的实现顺序

repo-side 当前主线建议按下面顺序推进，而不是先继续优化 tar 同步：

1. 新增 relay projection store 及其最小 schema
   具体规则见 `docs/plans/vps_relay_projection_store_schema_v0.1.md`
2. 在 PC 侧实现 projection publisher
   具体规则见 `docs/plans/vps_relay_projection_publisher_protocol_v0.1.md`
3. 在现有 runner / session-action / artifact / closeout 触发点挂双写
4. 给 Android facades 增加 shadow compare
   具体规则见 `docs/plans/vps_relay_projection_cutover_shadow_compare_v0.1.md`
5. 切 `sessions / snapshot / history / updates` 读源
6. 下线 shared task_root 在线依赖
7. 最后把 `sync companion` 降级为 operator-only 工具

## 12. 当前主线读法

从今天起，repo-side 对这条问题的主线读法应固定为：

- 不再继续投资“更聪明的 `task_root` 整包同步”
- 也不把“只同步 `_scheduler/` 和少数 JSON”读成长期最优解
- 正确方向是 relay-native projection store
- Android contract 首轮保持稳定
- `PC` 继续保留本地 execution truth
- relay 成为 Android-facing projection truth 的持有者

这不是补丁式运维优化，而是当前 `VPS-first` 主线必须完成的一次结构收敛。
