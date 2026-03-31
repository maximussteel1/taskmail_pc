# VPS Relay Projection Store Schema（v0.1）

## 状态

- 日期：2026-03-29
- 范围：冻结 relay-native projection store 的首轮 durable schema
- 目标：支撑 Android `sessions / session-snapshot / session-history / session-updates` 读面去掉对 relay-visible `shared task_root` 的在线依赖
- 相关文档：
  - `docs/plans/vps_relay_projection_store_mainline_v0.1.md`
  - `docs/plans/vps_relay_projection_publisher_protocol_v0.1.md`
  - `docs/plans/vps_relay_projection_cutover_shadow_compare_v0.1.md`
  - `docs/current/android_sessions_facade_contract.md`
  - `docs/current/android_session_snapshot_facade_contract.md`
  - `docs/current/android_session_history_rounds_contract.md`
  - `docs/current/android_session_updates_facade_contract.md`

## 1. 一句话约束

projection store 是 relay 侧的 Android-facing durable projection truth，不是 `task_root` 镜像，不保存 `thread_state.json` / `result.json` / raw mail 的原文影子。

## 2. 首轮边界

必须：

- 首轮存储介质按 `SQLite` 设计。
- session 级写入必须带 `projection_version`，并按 session 原子提交。
- schema 必须能独立回答：
  - `GET /v1/android/sessions`
  - `GET /v1/android/session-snapshot`
  - `GET /v1/android/session-history`
  - `WS /v1/android/session-updates`
- `latest_session_action` 继续从现有 `pc_command_store` 拼接，不迁入本 schema。
- `workspace inventory` 继续复用现有 `workspace_inventory_store`，不在本文件里重定义。

禁止：

- 把 `task_root` 下的 JSON 文件整体复制进 store。
- 把 PC 本地路径、线程目录相对路径、mail-specific 原文字段当作 Android 读面主键。
- 通过 relay 侧现扫目录来回填缺失字段。
- 在 schema 里缓存第二份 `latest_session_action` authoritative truth。

## 3. 存储级通用规则

### 3.1 会话 canonical identity

session 级对象的 canonical identity 固定为：

- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`

首轮约束：

- `(pc_id, workspace_id, session_id, thread_id)` 必须唯一。
- `thread_id` 必须全库唯一；若 publisher 试图写入重复 `thread_id`，relay 应视为 store conflict，而不是静默覆盖。

### 3.2 版本与时间戳

- `projection_version`：PC 生成的 session 级单调版本号；只在 Android-facing 业务投影发生变化时递增。
- `source_updated_at`：PC 侧业务对象的 authoritative 更新时间。
- `applied_at`：relay 把该条 projection durable upsert 成功提交到本地 store 的时间。

同一 logical state transition 下：

- 所有 session-scoped row 必须共享同一个 `projection_version`。
- relay 必须在单个数据库事务内提交这些 row。

### 3.3 JSON 字段

首轮允许使用 JSON 文本列承载以下投影：

- `question_state_json`
- `timeline_items_json`
- `process_items_json`
- `target_session_identity_json`

理由：

- 这些字段当前已经是 Android-facing contract 的稳定 payload 片段。
- 首轮不值得为了它们再拆出多张只服务单一读取路径的细粒度表。

## 4. 表与职责

### 4.1 `projection_ingest_receipts`

用途：

- 承接 projection publisher 的幂等去重。
- 检测“同一个 idempotency key 对应不同 payload”的冲突。
- 为 shadow compare / operator 排障保留最小摄入证据。

主键与唯一键：

- 主键：`id`
- 唯一键：`message_family + idempotency_key`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | text | relay 本地行 id |
| `message_family` | text | 例如 `session_projection_upsert` |
| `idempotency_key` | text | publisher 提供的稳定幂等键 |
| `batch_id` | text | 所属 batch id |
| `pc_id` | text | 来源 PC |
| `workspace_id` | text nullable | 非 session scope 时可空 |
| `session_id` | text nullable | 非 session scope 时可空 |
| `thread_id` | text nullable | 非 session scope 时可空 |
| `projection_version` | integer nullable | session-scoped message 必填 |
| `payload_sha256` | text | 用于检测同键不同义 |
| `connection_epoch` | integer | 来源 websocket 连接 epoch |
| `source_sent_at` | text | publisher 发送时间 |
| `applied_at` | text | relay commit 时间 |

### 4.2 `projection_sessions`

用途：

- `GET /v1/android/sessions` 的主读表。
- `GET /v1/android/session-snapshot` 的 summary / detail 头部主读表。
- `WS /v1/android/session-updates` 的主推送版本锚点。

主键与索引：

- 主键：`session_key`
- 唯一键：`pc_id + workspace_id + session_id + thread_id`
- 唯一键：`thread_id`
- 索引：`session_id`
- 索引：`workspace_id + lifecycle`
- 索引：`lifecycle + last_progress_at desc + updated_at desc`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_key` | text | relay 本地稳定键 |
| `pc_id` | text | canonical identity |
| `workspace_id` | text | canonical identity |
| `session_id` | text | canonical identity |
| `thread_id` | text | canonical identity |
| `session_name` | text | 列表 / detail 共用 |
| `backend` | text | 列表 / detail 共用 |
| `backend_transport` | text nullable | 列表字段 |
| `profile` | text | 列表字段 |
| `permission` | text | 列表字段 |
| `repo_path` | text | locator / 列表 / detail |
| `workdir` | text nullable | locator / 列表 / detail |
| `list_status` | text | 对应 `GET /sessions -> session.status` |
| `snapshot_status` | text | 对应 `session_snapshot.status` |
| `lifecycle` | text | 列表 / detail 共用 |
| `current_task_id` | text nullable | 列表 / detail 共用 |
| `queued_task_id` | text nullable | 列表 / detail 共用 |
| `pending_task_count` | integer | 列表字段 |
| `last_summary` | text nullable | 列表 / detail 共用 |
| `last_active_at` | text nullable | 列表 / detail 共用 |
| `last_progress_at` | text nullable | 列表 / detail 共用 |
| `paused_from_status` | text nullable | detail 字段 |
| `backend_session_id` | text nullable | 列表字段 |
| `backend_session_resumable` | integer | 布尔投影，0/1 |
| `question_state_json` | text nullable | detail 字段 |
| `timeline_items_json` | text nullable | detail 字段 |
| `created_at` | text | 列表字段 |
| `updated_at` | text | 列表字段 |
| `projection_version` | integer | 当前 session 头部版本 |
| `source_updated_at` | text | PC 侧源更新时间 |
| `applied_at` | text | relay commit 时间 |

规则：

- `list_status` 与 `snapshot_status` 必须拆开存，不能在 read path 里互相猜。
- `projection_version` 是 `session-updates` 的唯一热路径版本锚点。
- detail payload 的 `history_rounds` 不在本表内保存。

### 4.3 `projection_history_rounds`

用途：

- `GET /v1/android/session-history`
- `GET /v1/android/session-snapshot -> history_rounds`

主键与索引：

- 主键：`round_key`
- 唯一键：`pc_id + session_id + round_id`
- 唯一键：`pc_id + session_id + task_id`
- 索引：`session_key + round_sort_at + task_id`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `round_key` | text | relay 本地稳定键 |
| `session_key` | text | 外键到 `projection_sessions.session_key` |
| `pc_id` | text | 冗余 identity，便于查表 |
| `workspace_id` | text | 冗余 identity，便于查表 |
| `session_id` | text | 冗余 identity，便于查表 |
| `thread_id` | text | 冗余 identity，便于查表 |
| `task_id` | text | 对应当前 durable round 的业务 task |
| `round_id` | text | 当前固定形态可取 `hist_round_<task_id>` |
| `round_sort_at` | text | chronological 排序锚点 |
| `status` | text | 对应当前 `history_rounds[].status` |
| `speaker_label` | text | 对应当前 payload |
| `input_text` | text nullable | `input.text` |
| `process_items_json` | text | `process.items` |
| `result_text` | text | `result.text` |
| `projection_version` | integer | 该 round 最后一次被 session 版本覆盖时的版本号 |
| `source_updated_at` | text | PC 侧 round authoritative 更新时间 |
| `applied_at` | text | relay commit 时间 |

规则：

- `round_number` 不单独落表；read path 必须按 `round_sort_at + task_id` 正序编号，再 reverse 为最新在前。
- queued round、running round、awaiting_user_input round、terminal round 都落在同一张表，不分临时表。

### 4.4 `projection_round_attachments`

用途：

- 为 `history_rounds[].input.attachments`
- 为 `history_rounds[].result.attachments`

主键与索引：

- 主键：`round_attachment_key`
- 唯一键：`round_key + attachment_role + ordinal`
- 索引：`artifact_id`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `round_attachment_key` | text | relay 本地稳定键 |
| `round_key` | text | 外键到 `projection_history_rounds.round_key` |
| `attachment_role` | text | `input` 或 `result` |
| `ordinal` | integer | 当前 round 内稳定顺序 |
| `attachment_id` | text | Android-facing attachment id |
| `artifact_id` | text nullable | 若该附件来自 artifact truth，则回填 |
| `display_name` | text | Android-facing 字段 |
| `content_type` | text | Android-facing 字段 |
| `size_bytes` | integer nullable | Android-facing 字段 |
| `is_image` | integer | 布尔投影，0/1 |

规则：

- 该表只保存当前 Android contract 需要的附件元数据。
- 不保存 `saved_path`、本地相对路径、mail `cid:` 等字段。

### 4.5 `projection_artifact_refs`

用途：

- 保存 artifact truth 到 `/v1/files` 的 transport-facing 绑定。
- 让 relay 不再依赖远端 `artifact_index.json` / `external_delivery_index.json` 影子文件。

主键与索引：

- 主键：`artifact_id`
- 索引：`session_key`
- 索引：`task_id`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `artifact_id` | text | artifact truth 稳定 id |
| `session_key` | text | 外键到 `projection_sessions.session_key` |
| `pc_id` | text | 冗余 identity |
| `workspace_id` | text | 冗余 identity |
| `session_id` | text | 冗余 identity |
| `thread_id` | text | 冗余 identity |
| `task_id` | text | 所属 round/task |
| `display_name` | text | 展示名 |
| `content_type` | text | MIME |
| `size_bytes` | integer nullable | 大小 |
| `is_image` | integer | 布尔投影，0/1 |
| `file_id` | text nullable | `/v1/files` file id |
| `download_ref` | text nullable | Android / operator 消费引用 |
| `download_ref_source` | text nullable | 例如 `external_delivery_index.file_surface` |
| `provider` | text nullable | 当前 external-delivery provider |
| `expires_at` | text nullable | download ref 过期时间 |
| `projection_version` | integer | 最后一次绑定更新对应的 session 版本 |
| `source_updated_at` | text | PC 侧源更新时间 |
| `applied_at` | text | relay commit 时间 |

规则：

- 首轮 `history_rounds` payload 仍只暴露附件元数据；`download_ref` 是 relay 内部主真相，供 detail 扩展和 operator 读取。
- 该表不替代 PC 本地 `artifact_index.json` 的 artifact truth 角色。

### 4.6 `projection_closeouts`

用途：

- 保存 canonical outcome anchors。
- 替代 relay 对 `canonical_summary.json` / `session_action_closeout.json` 镜像文件的在线依赖。

主键与索引：

- 主键：`closeout_key`
- 索引：`session_key + generated_at desc`
- 索引：`request_id`
- 索引：`receipt_id`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `closeout_key` | text | publisher 生成的稳定 closeout key |
| `session_key` | text | 外键到 `projection_sessions.session_key` |
| `pc_id` | text | 冗余 identity |
| `workspace_id` | text | 冗余 identity |
| `session_id` | text | 冗余 identity |
| `thread_id` | text | 冗余 identity |
| `task_id` | text nullable | 对应 run/task |
| `request_id` | text nullable | direct action request id |
| `packet_id` | text nullable | current closeout anchor |
| `receipt_id` | text nullable | current closeout anchor |
| `action_type` | text nullable | reply/status/pause/... |
| `target_session_identity_json` | text nullable | session action target |
| `last_summary` | text nullable | canonical summary 摘要 |
| `terminal_mail_message_id` | text nullable | terminal mail anchor |
| `terminal_mail_subject` | text nullable | terminal mail anchor |
| `generated_at` | text | closeout 形成时间 |
| `projection_version` | integer | 若本 closeout 同时影响 Android-facing session payload，则等于对应 session 版本；否则可复用当前版本 |
| `source_updated_at` | text | PC 侧源更新时间 |
| `applied_at` | text | relay commit 时间 |

规则：

- 首轮 closeout 不直接成为 Android detail 主 payload 字段，但必须在 relay 侧持久化，避免回退到远端文件扫描。

### 4.7 `projection_probe_observations`

用途：

- 保存 `transport_probe` 的 PC observation。
- 让 relay-side `transport_probe` 不再依赖 `_mailbox/transport_probes/*.json` 影子。

主键与索引：

- 主键：`probe_id`
- 索引：`request_id`
- 索引：`packet_id`
- 索引：`observed_at desc`

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `probe_id` | text | stable probe id |
| `pc_id` | text nullable | 若已知则填写 |
| `request_id` | text nullable | 控制面 request id |
| `packet_id` | text nullable | 控制面 packet id |
| `receipt_id` | text nullable | receipt anchor |
| `mailbox_message_id` | text nullable | 观察到的 mail message id |
| `summary_text` | text | observation 摘要 |
| `observation_status` | text | 例如 observed / timeout / failed |
| `observed_at` | text | PC observation 时间 |
| `payload_json` | text | 当前 probe payload 的 durable 结果 |
| `applied_at` | text | relay commit 时间 |

## 5. Android 读面映射

| Android 读面 | 读取对象 |
| --- | --- |
| `GET /v1/android/sessions` | `projection_sessions` |
| `GET /v1/android/session-snapshot` | `projection_sessions` + `projection_history_rounds` + `projection_round_attachments` + `pc_command_store` |
| `GET /v1/android/session-history` | `projection_sessions` + `projection_history_rounds` + `projection_round_attachments` |
| `WS /v1/android/session-updates` | `projection_sessions.projection_version` 变化触发，再用与 `session-snapshot` 同一 builder 取数 |

补充规则：

- `latest_session_action` 继续只从 `pc_command_store` 读取。
- `pc_id` 不再通过 command ledger / workspace inventory 猜测；projection store 里的 `pc_id` 直接作为 session authoritative identity。

## 6. 首轮不落表的内容

以下内容首轮明确不作为 projection store row 保存：

- `thread_state.json` / `result.json` / `task snapshot` 原文
- raw mail payload
- `summary.txt` 原文全文
- output chunk transcript
- `latest_session_action` 的第二份持久化副本
- 任何 PC 本地绝对路径

## 7. 对实现的直接约束

- session-scoped batch commit 时，必须先写 round / attachment / artifact / closeout，再更新 `projection_sessions.projection_version`，并在事务提交后才允许触发 websocket push。
- `projection_sessions` row 必须始终能单独构出：
  - list `session`
  - detail `locator`
  - detail `session`
  - detail `session_snapshot` 除 `history_rounds` 和 `latest_session_action` 外的所有字段
- history read path 不得再访问 remote `task_root`。
