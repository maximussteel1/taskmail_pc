# VPS Relay Projection Store 切换与 Shadow Compare 方案（v0.1）

## 状态

- 日期：2026-03-29
- 范围：冻结 Android live read path 从 `task_root` 镜像切到 relay-native projection store 的切换步骤与对账规则
- 目标：在不改 Android contract 的前提下，下线 Android 读面对 `shared task_root` sync 的在线依赖
- 当前状态：
  - 该文档已降级为历史切换方案记录
  - `2026-03-29` owner 决策改为直接切主链，不再保留 shadow compare/read-source 开关
  - current truth 以 `docs/current/android_*` 与 `docs/current/README.md` 为准
- 相关文档：
  - `docs/plans/vps_relay_projection_store_mainline_v0.1.md`
  - `docs/plans/vps_relay_projection_store_schema_v0.1.md`
  - `docs/plans/vps_relay_projection_publisher_protocol_v0.1.md`
  - `docs/current/android_sessions_facade_contract.md`
  - `docs/current/android_session_snapshot_facade_contract.md`
  - `docs/current/android_session_history_rounds_contract.md`
  - `docs/current/android_session_updates_facade_contract.md`

## 1. 一句话约束

> 历史说明：下面的双写 / shadow compare / read-source 开关规则描述的是原计划切换口径，不再代表当前代码路径。

切换顺序必须是：先双写，再 shadow compare，再切 read source，最后把 `sync companion` 退出 Android 主链；任何“先停 companion 再补投影”的做法都不在主线内。

## 2. 切换原则

必须：

- Android endpoint、locator、payload shape、错误码保持不变。
- 在 projection store 稳定前，task-root-based read path 继续作为 serving path。
- shadow compare 比较的是业务 payload，不是内部实现细节。
- `session-updates` 读源切换必须晚于 HTTP read path 切换。

禁止：

- 跳过双写直接切 Android facade。
- 把“projection store 看起来更合理”的差异自动视为可接受。
- 在 read cutover 前把 `manage_mail_runner.ps1` 的 companion 健康检查删掉。

## 3. 推荐开关

首轮实现建议显式保留以下 relay 侧开关：

| 开关 | 取值 | 作用 |
| --- | --- | --- |
| `relay_projection_store_enabled` | `true/false` | 是否初始化 projection store |
| `relay_projection_store_write_enabled` | `true/false` | 是否消费 publisher 并写 store |
| `relay_projection_store_shadow_compare` | `true/false` | 是否做双路径对账 |
| `relay_android_http_read_source` | `task_root / shadow / projection_store` | HTTP facade 读源 |
| `relay_android_ws_read_source` | `task_root_poll / projection_store` | WS facade 读源 |

约束：

- `shadow` 模式表示“用旧路径对外服务，同时后台构建新 payload 并比较”。

## 4. 迁移阶段

### 阶段 0：冻结规则

输入：

- schema / publisher / cutover 文档冻结

退出条件：

- 三份文档已经挂进当前主线阅读入口

### 阶段 1：写路径双写

动作：

- PC 保持写本地 `task_root`
- 同时开始发 `projection_batch`
- relay 开始落 projection store，但 Android 仍只读旧路径

退出条件：

- representative session 在 projection store 中可完整复原 list/detail/history
- 旧路径行为无回退

### 阶段 2：HTTP Shadow Compare

动作：

- `GET /v1/android/sessions`
- `GET /v1/android/session-snapshot`
- `GET /v1/android/session-history`

对外继续返回旧路径 payload，同时后台构建 projection-store payload 做 compare。

退出条件：

- 所有 blocking diff 清零
- stale version / idempotency conflict 问题清零

### 阶段 3：WS Shadow Compare

动作：

- `WS /v1/android/session-updates` 继续用旧路径轮询推送
- 每次推送前并行构建 projection-store snapshot 并比较

退出条件：

- 首帧 snapshot、增量变化 snapshot 均无 blocking diff
- 不存在“store 已更新但 websocket 推送晚于旧路径可观察窗口”的明显新鲜度回退

### 阶段 4：HTTP Cutover

切换顺序：

1. `GET /v1/android/sessions`
2. `GET /v1/android/session-snapshot` 与 `GET /v1/android/session-history` 一起切

约束：

- `session-snapshot` 与 `session-history` 必须同一轮切换，避免 detail 与 history round truth 脱钩。

退出条件：

- Android detail/list/history 在 companion 仍运行时已稳定只读 projection store

### 阶段 5：WS Cutover

动作：

- `WS /v1/android/session-updates` 改成 store-driven push

退出条件：

- detail 实时刷新已不依赖 task-root 轮询
- reconnect 首帧与 HTTP snapshot 同构

### 阶段 6：Companion 退出在线主链

动作：

- 关闭 Android facade 对 remote `task_root` 的在线依赖
- 更新维护脚本与 current 文档，不再把 companion 存活视为 Android 主链健康前提

允许保留：

- operator 手动导出
- 故障取证
- 一次性 migration snapshot

## 5. Compare 对象与规范化规则

### 5.1 `GET /v1/android/sessions`

比较对象：

- 顶层：
  - `session_count`
  - `sessions`
- 单项 session：
  - 全部业务字段

允许忽略：

- `snapshot_id`
- `generated_at`
- `refresh_after_seconds`

必须完全一致：

- 返回顺序
- 默认 active-only 过滤结果
- `include_ended=true` 后的结果

### 5.2 `GET /v1/android/session-snapshot`

比较对象：

- `locator`
- `session`
- `session_snapshot`

允许忽略：

- `snapshot_id`
- `generated_at`

必须完全一致：

- `pc_id / workspace_id / session_id / thread_id`
- `session.status`
- `session_snapshot.status`
- `question_state`
- `timeline_items`
- `latest_session_action`
- `history_rounds`

### 5.3 `GET /v1/android/session-history`

比较对象：

- `history_rounds`

允许忽略：

- `snapshot_id`
- `generated_at`

必须完全一致：

- round 数量
- round 顺序
- `round_id`
- `round_number`
- `created_at`
- `status`
- `speaker_label`
- `input`
- `process`
- `result`

### 5.4 `WS /v1/android/session-updates`

比较对象：

- 首帧 `session_snapshot`
- 后续每一帧 `session_snapshot`

允许忽略：

- `subscription_id`
- `sent_at`
- `snapshot_id`
- `generated_at`

必须完全一致：

- `message_type`
- `locator`
- `session`
- `session_snapshot`

## 6. Blocking Diff 规则

以下差异一律视为 blocking：

- locator 任何字段不同
- session list 返回顺序不同
- `status / lifecycle / current_task_id / queued_task_id / pending_task_count` 不同
- `question_state` 不同
- `timeline_items` 个数、顺序或字段不同
- `latest_session_action` 缺失、额外出现或字段不同
- `history_rounds` 个数、顺序或字段不同
- attachment 元数据不同
- `pc_id` 在旧路径为 `null`、新路径却非 `null`
- `404/409/503` 等错误码或错误体不同

以下差异可忽略：

- 服务端生成的随机/时钟型 envelope 字段

当前首轮没有“看起来更合理，所以先放过”的白名单。

## 7. Shadow Compare 的执行方式

HTTP：

1. 旧路径先生成 authoritative response
2. 新路径生成候选 response
3. 两者做 normalize 后比较
4. 记录 diff 结果
5. 仍返回旧路径结果

WS：

1. 旧路径决定是否应推送新帧
2. 若需要推送，先构建旧路径 snapshot
3. 再构建 projection-store snapshot
4. 做 normalize compare
5. 无论 compare 结果如何，在 cutover 前都只推旧路径 snapshot

记录要求：

- diff log 必须能定位：
  - endpoint
  - locator
  - old/new read source
  - 归一化后的字段路径
  - old/new 值摘要
- shadow compare 失败不能把 Android 请求直接打挂，但必须可观测。

## 8. 首轮错误码兼容

尽管 read source 切换后 Android 已不再依赖 `task_root`，首轮 facade 仍保持当前 contract error code：

- `401 unauthorized`
- `400 invalid_payload`
- `404 session_not_found`
- `409 session_binding_unresolved`
- `409 workspace_identity_mismatch`
- `409 session_identity_mismatch`
- `503 task_root_unavailable`

这里的 `503 task_root_unavailable` 在 cutover 后应读作：

- Android-facing 的兼容错误码
- 不再字面等于“remote task_root 镜像不可读”

error code 重命名不是本轮目标。

## 9. 最小验收矩阵

至少覆盖以下样本：

| 场景 | `/sessions` | `/session-snapshot` | `/session-history` | `session-updates` |
| --- | --- | --- | --- | --- |
| queued session | 必测 | 必测 | 必测 | 必测 |
| running session | 必测 | 必测 | 必测 | 必测 |
| awaiting_user_input | 必测 | 必测 | 必测 | 必测 |
| paused / resumed | 必测 | 必测 | 必测 | 必测 |
| terminal success with artifacts | 必测 | 必测 | 必测 | 必测 |
| terminal failed / killed | 必测 | 必测 | 必测 | 必测 |
| latest_session_action continuity 存在 | 可选 | 必测 | 可选 | 必测 |
| `include_ended=true` | 必测 | 可选 | 可选 | 不适用 |

通过条件：

- 所有必测样本 blocking diff 为 0
- WS 首帧与同 locator 的 HTTP snapshot 对齐
- artifact-bearing round 的附件元数据完全一致
- reconnect 后不会因为 projection store 读 path 丢失当前态

## 10. Companion 退场门槛

只有在以下条件同时成立后，`sync companion` 才能退出 Android 主链：

1. `sessions / session-snapshot / session-history / session-updates` 全部已切到 projection store
2. `latest_session_action` continuity 未回退
3. history rounds / artifact metadata 不再访问 remote `task_root`
4. `transport_probe` observation 不再访问 remote `_mailbox/transport_probes`
5. `manage_mail_runner.ps1 status` 已不再把 companion 当作 Android 主链健康前提
6. `docs/current/*` 已更新为新的 current truth

## 11. 不该做的切换方式

以下做法都不属于当前主线：

- 先把 tar sync 改成 `rsync`，再继续长期依赖它
- 只切 `/sessions`，把 detail/history 继续绑在 task-root path 上
- 让 HTTP 用 projection store、但 WS 继续无限期轮询 task-root
- 在 shadow compare 期间把“projection store 结果更完整”当作差异豁免
