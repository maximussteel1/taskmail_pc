# VPS Relay Projection Publisher Protocol 与 Trigger Matrix（v0.1）

## 状态

- 日期：2026-03-29
- 范围：冻结 PC -> relay 的 projection publisher 首轮协议与触发点
- 目标：让 relay 通过 `pc-control` 长连接接收 durable Android-facing projection，而不是接收 `task_root` 目录镜像
- 相关文档：
  - `docs/plans/vps_relay_projection_store_mainline_v0.1.md`
  - `docs/plans/vps_relay_projection_store_schema_v0.1.md`
  - `docs/plans/vps_relay_projection_cutover_shadow_compare_v0.1.md`
  - `docs/current/android_sessions_facade_contract.md`
  - `docs/current/android_session_snapshot_facade_contract.md`
  - `docs/current/android_session_history_rounds_contract.md`
  - `docs/current/android_session_updates_facade_contract.md`

## 1. 一句话约束

projection publisher 首轮必须复用现有 `pc-control` websocket；PC 推送的是 session-scoped projection batch，不是文件树、不是目录 diff、也不是“告诉 relay 自己去扫 task_root”。

## 2. 首轮协议原则

必须：

- 继续走现有 `pc-control` 长连接。
- 用单独的 `projection_batch` message 承载 projection 对象。
- session-scoped batch 必须原子表达“一次业务状态变更后的完整投影结果”。
- 所有会影响 Android payload 的写入都必须带 `projection_version`。
- 所有 message item 都必须带稳定 `idempotency_key`。

禁止：

- 为 projection 再开第二条 sync daemon 网络链路。
- 把 `task_root` 文件路径作为 wire payload 主字段。
- 让 relay 根据 message hint 自己再去读 PC 的 `task_root`。
- 用目录 mtime 或 tar 替换顺序推断业务先后。

## 3. Wire Envelope

首轮新增的 wire message 顶层固定为：

- `message_type = projection_batch`
- `schema_version = taskmail-pc-projection-batch-v1`

顶层字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `message_type` | 是 | 固定 `projection_batch` |
| `schema_version` | 是 | 固定 `taskmail-pc-projection-batch-v1` |
| `batch_id` | 是 | 本次 batch 稳定 id |
| `pc_id` | 是 | 来源 PC |
| `connection_epoch` | 是 | 当前 pc-control 连接 epoch |
| `sent_at` | 是 | batch 发送时间 |
| `scope` | 是 | `session` 或 `probe` |
| `workspace_id` | `scope=session` 时必填 | canonical identity |
| `session_id` | `scope=session` 时必填 | canonical identity |
| `thread_id` | `scope=session` 时必填 | canonical identity |
| `projection_version` | `scope=session` 时必填 | session 级单调版本 |
| `items` | 是 | projection items |

## 4. Item Family

首轮固定 4 类 item family：

1. `session_projection_upsert`
2. `session_round_upsert`
3. `session_closeout_upsert`
4. `transport_probe_observation_upsert`

其中：

- `scope=session` 的 batch 必须且只能包含：
  - 1 个 `session_projection_upsert`
  - 0..N 个 `session_round_upsert`
  - 0..N 个 `session_closeout_upsert`
- `scope=probe` 的 batch 只包含：
  - 1 个 `transport_probe_observation_upsert`

额外约束：

- 首轮不定义单独的 `session_projection_snapshot` family。
- reconnect 后的恢复通过“重发当前最新 batch”完成，而不是增加第二套 snapshot RPC。

## 5. Session-Scoped Batch 原子性

同一个 `scope=session` batch 内：

- 所有 item 必须共享同一个 `{pc_id, workspace_id, session_id, thread_id, projection_version}`。
- relay 必须在一个数据库事务里完整落表。
- 事务未提交前，不得触发 `session-updates` 推送。
- 事务提交后，`projection_sessions.projection_version` 才能变成新版本。

这条规则的目的只有一个：

- 避免 Android detail 读到“session 头已经变成 version=17，但 round 还是 version=16”的半写状态。

## 6. Item 载荷规则

### 6.1 `session_projection_upsert`

职责：

- 刷新 session head。
- 作为 session batch 的 commit anchor。

必须覆盖字段：

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
- `paused_from_status`
- `backend_session_id`
- `backend_session_resumable`
- `question_state`
- `timeline_items`
- `created_at`
- `updated_at`
- `source_updated_at`
- `idempotency_key`

规则：

- `question_state` 与 `timeline_items` 是 projection 结果，不是 raw thread state dump。
- 即使本次变化主要发生在 round 或 artifact，也必须同时重发 1 个最新 `session_projection_upsert`。

### 6.2 `session_round_upsert`

职责：

- 刷新当前 session 下某一轮 `history_rounds` durable projection。
- 可同时携带 input/result attachments 与 artifact refs。

必须覆盖字段：

- `task_id`
- `round_id`
- `round_sort_at`
- `status`
- `speaker_label`
- `input_text`
- `input_attachments`
- `process_items`
- `result_text`
- `result_attachments`
- `artifact_refs`
- `source_updated_at`
- `idempotency_key`

规则：

- `round_number` 不上 wire；由 relay 读 path 在排序后计算。
- queued round 与 running round 都要能用同一个 family upsert。
- `input_attachments` / `result_attachments` 只带 Android-facing 元数据。
- 若 `/v1/files` 绑定稍后才拿到，publisher 必须重发新的 `session_round_upsert`，并 bump `projection_version`。

### 6.3 `session_closeout_upsert`

职责：

- 刷新 canonical outcome anchor。

必须覆盖字段：

- `closeout_key`
- `task_id`
- `request_id`
- `packet_id`
- `receipt_id`
- `action_type`
- `target_session_identity`
- `last_summary`
- `terminal_mail_message_id`
- `terminal_mail_subject`
- `generated_at`
- `source_updated_at`
- `idempotency_key`

规则：

- closeout 首轮不要求直接出现在 Android payload 中。
- 若 closeout 同时改变 `last_summary` 或其他 Android-facing 字段，必须与新的 `session_projection_upsert` 放进同一个新版本 batch。
- 若只是补充 operator/canonical anchor，而 Android payload 不变，可复用当前 `projection_version` 发一个 metadata-only batch；relay 不应据此触发 `session-updates` 推送。

### 6.4 `transport_probe_observation_upsert`

职责：

- durable 保存 `transport_probe` 的 PC observation。

必须覆盖字段：

- `probe_id`
- `request_id`
- `packet_id`
- `receipt_id`
- `mailbox_message_id`
- `summary_text`
- `observation_status`
- `observed_at`
- `payload`
- `idempotency_key`

规则：

- probe batch 不带 session identity。
- probe batch 不参与 `projection_version` 管理。

## 7. 幂等、版本与乱序语义

### 7.1 幂等键

每个 item 的 `idempotency_key` 必须稳定，推荐形态：

- `session_projection_upsert`：
  - `sess_head:{pc_id}:{session_id}:{projection_version}`
- `session_round_upsert`：
  - `sess_round:{pc_id}:{session_id}:{task_id}:{projection_version}`
- `session_closeout_upsert`：
  - `sess_closeout:{pc_id}:{session_id}:{closeout_key}:{projection_version}`
- `transport_probe_observation_upsert`：
  - `probe_obs:{probe_id}:{observed_at}`

relay 语义：

- 首次见到该 key：正常应用。
- 再次见到同 key 且 payload 等价：no-op 成功。
- 再次见到同 key 但 payload 不等价：视为 conflict。

### 7.2 版本规则

- `projection_version` 由 PC 维护，relay 不自行分配。
- session 级版本不要求连续，但必须单调递增。
- relay 遇到旧版本 `< 当前 version` 的 session batch，必须按 stale no-op 处理。
- relay 遇到相同版本 `= 当前 version` 的 session batch，必须走幂等比较，不得盲写。
- relay 遇到新版本 `> 当前 version` 的 session batch，只要 batch 自洽且 identity 匹配，就应接受，不要求补齐中间版本。

### 7.3 何时 bump `projection_version`

必须 bump：

- `GET /v1/android/sessions` 任一业务字段变化
- `GET /v1/android/session-snapshot` 任一业务字段变化
- `GET /v1/android/session-history` 任一 round/attachment 业务字段变化
- `WS /v1/android/session-updates` 应该让 detail 页看到的新状态变化

不得单独 bump：

- `snapshot_id`
- `generated_at`
- `sent_at`
- `subscription_id`
- 只服务本地证据保留、但不影响 Android 当前 payload 的文件写入

## 8. Trigger Matrix

| 业务变化点 | 推荐挂点 | 发送 item | 是否 bump version | 说明 |
| --- | --- | --- | --- | --- |
| create-session 初始落盘 | `thread_store.save_thread_state()` 首次写入后 | `session_projection_upsert` + 当前 task 的 `session_round_upsert` | 是 | 首帧需要能回答 list/detail/history |
| 新任务进入 queued | `runner.py` 中 `queued_task_id / queued_snapshot_file` 落稳后 | `session_projection_upsert` + queued round 的 `session_round_upsert` | 是 | 保持当前 queued round 可见 |
| queued 任务转 running | `runner.py` 切换 `current_task_id`、清空 `queued_*` 后 | `session_projection_upsert` + current round 的 `session_round_upsert` | 是 | 不能只更新 session 头 |
| running 中 summary / progress / question_state 变化 | 每次 `save_thread_state()` 后，若 Android-facing 字段变了 | `session_projection_upsert`；若当前 round 的 `process/result/status` 同步变了，再带 `session_round_upsert` | 是 | 触发条件按投影内容，不按文件存在 |
| run 进入 awaiting_user_input | `runner._finalize_thread_state()` 将 `history_files`、pending questions 写稳后 | `session_projection_upsert` + 该 task 的 `session_round_upsert` | 是 | 这轮已经有 durable result |
| pause / resume | 对应 `save_thread_state()` 成功后 | `session_projection_upsert` + 必要时 `session_round_upsert` | 是 | `paused_from_status` / timeline 变化必须带出去 |
| terminal done / failed / killed | `runner._finalize_thread_state()` 后 | `session_projection_upsert` + 终态 `session_round_upsert` | 是 | 终态 summary/history 必须一致 |
| artifact `/v1/files` 引用就绪 | `outbound/service.py` 中 artifact index / external delivery 已形成后 | `session_projection_upsert` + 带最新附件/refs 的 `session_round_upsert` | 是 | history 附件变化属于 Android-facing payload 变化 |
| canonical summary / session action closeout 就绪 | `outbound/service.py`、`session_action_closeout.py` 持久化成功后 | `session_closeout_upsert`；若同时改了 `last_summary` 等，再加 `session_projection_upsert` | 视 Android payload 是否变化 | closeout-only 可不触发 push |
| transport probe observation 形成 | `transport_probe_mail.record_transport_probe_observation()` 后 | `transport_probe_observation_upsert` | 否 | 不属于 session 版本流 |
| pc-control 重连恢复 | 连接重建后枚举 active/recent session | 重发每个 session 当前最新 batch；必要时重发近期 probe observation | 否，复用原版本 | 首轮不定义独立 snapshot family |

## 9. Repo 级代码锚点

这些位置是首轮最适合挂 publisher 的 repo 锚点：

- `mail_runner/thread_store.py`
  - `save_thread_state()`
  - 这是 session head 双写的主 seam，因为它同时刷新 `SessionState / WorkspaceState`
- `mail_runner/runner.py`
  - `_finalize_thread_state()`
  - 这是 result durable、history_files 进入真相层后的主 seam
- `mail_runner/outbound/service.py`
  - artifact index、external delivery、canonical summary 成形后的 seam
- `mail_runner/session_action_closeout.py`
  - session action closeout durable seam
- `mail_runner/transport_probe_mail.py`
  - probe observation durable seam

## 10. 对实现的直接约束

- publisher 判断“要不要发新 batch”时，比较的是 projection payload 是否变化，不是文件 mtime。
- session-scoped batch 的构造顺序必须是：
  1. 先构 round / attachment / artifact / closeout projection
  2. 再构最新 `session_projection_upsert`
  3. 最后一次性发送 batch
- relay 不负责修补缺字段；若 publisher 发不出当前完整投影，就应当 fail fast，而不是让 relay 回扫 `task_root` 兜底。
