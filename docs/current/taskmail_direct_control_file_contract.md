# TaskMail Direct Control And File Contract

## Status

- Date: 2026-03-24
- Scope: 当前 relay 暴露的 TaskMail 窄范围 direct surface、bridge/direct-result 语义、`/v1/files` file surface，以及 closeout 证据落盘
- Role: 当前仓库里与 TaskMail direct relay/control/file 行为直接对应的 canonical current protocol 文档

## 1. 位置

当前仓库仍是 mail-first 系统，但已经不再是“mail-only”系统。

当前正式支持的 direct surface 仅限以下几类：

- direct `new_task`
- bootstrap `[SYNC]` `v1` / `v2`
- shared `/control` bootstrap `v2`
- shared `/control` relay-side `transport_probe`
- current-session direct `/status`
- current-session plain direct `reply`
- active-session detail read sidecar
- relay `/v1/files` oversized-artifact file surface

补充约束：

- PC 仍然是 task execution truth
- mail 仍然是默认控制面、receipt truth、artifact/history truth
- direct result 当前只出现在 `/control` bootstrap `[SYNC]` `v2` 与 relay-side `transport_probe`
- 任何未在本文件中列出的 direct surface，都不应视为当前行为

## 2. 当前已支持的 Direct Surface

### 2.1 Direct `new_task`

- 当前 schema 是 `phase2-direct-outbound-contract-v1`
- 当前只接受 action=`new_task`
- accepted path 会桥接回 canonical 首封任务邮件入口，不会绕开现有 `Repo:` / `Task:` 语义
- user-visible `[ACCEPTED]` / `[RUNNING]` / terminal mail 仍沿用当前邮件链路与状态语义
- packet store 当前会把 accepted、fallback-classified rejection、hard rejection 统一持久化；accepted 后续失败还会保留 `last_error_code` / `last_error_message`

### 2.2 Bootstrap `[SYNC]`

- 当前支持两种 direct bootstrap 读法：
  - `taskmail-bootstrap-control-contract-v1`
  - `taskmail-bootstrap-control-contract-v2`
- `v1` 是 bridge-to-mail：
  - relay 接受后桥接回 canonical `[SYNC]` mail ingress
  - 最终结果仍由邮箱中的 `[SYNC] Project Folder List` 提供
- `v2` 是 direct-result：
  - relay 返回 `packet_ack`
  - 随后返回 `bootstrap_result`
  - accepted `v2` 不会额外生成 `[SYNC] Project Folder List` 邮件
- 当前 repo 中，`v2` 的 local truth 读取 runner config 里的 `project_sync_roots`，并以已配置 `task_root` 作为本地 truth 可用的运行时信号
- `bootstrap_result.sync_project_folders_result.canonical_body_text` 与 canonical `[SYNC]` 回复正文保持同一业务语义
- bootstrap direct path 不创建 task/thread/session，也不进入 thread/session projection

### 2.3 Shared `/control` Shell

- 当前 endpoint 是 shared `/control` websocket
- 当前与 `/relay`、`/v1/files` 复用同一 `Authorization: Bearer <transport_token>` 认证路径
- 当前保留 `hello / hello_ack`
- `hello_ack` 当前额外回告：
  - `transport_token_id`
  - `accepted_payload_schemas`
- 当前 `accepted_payload_schemas` 按 runtime 已 provision 的 handler 动态回告；当前已实现的 schema 只有：
  - `taskmail-bootstrap-control-contract-v2`
  - `taskmail-transport-probe-payload-v1`
- 当前 `/control` 只支持三类 business frame：
  - `ping -> pong`
  - `command(sync_project_folders) -> command_ack -> result(sync_project_folders_result)`
  - `command(transport_probe) -> command_ack -> event* -> result(transport_probe_result)`
- 当前 `/control` 的 bootstrap `command` 语义仍映射到 `taskmail-bootstrap-control-contract-v2`
- 当前 `/control transport_probe` 的 direct result 仍只落 relay-side mail-bridge harness：
  - 只支持 `scenario=android_direct_ping_to_vps_to_pc`
  - 只支持 `direction=android_to_pc`
  - 只支持 `transport_kind=mail`
  - 只支持 text-only payload；当前不支持 `artifacts`
  - relay 在 mail 提交成功后，当前会按 `timeout_seconds` 轮询 relay-visible task root 下的 `_mailbox/transport_probes/<probe_id>.json`
  - `result.status=completed` 且 `result.payload.outcome=observed` 表示 relay 已读到与当前 `request_id/packet_id/trace_id/transport_message_id` 对齐的 PC mailbox observation
  - `result.status=partial` 且 `result.payload.outcome=timed_out` 表示 relay 已提交 deterministic probe mail，但在超时前没有读到匹配 evidence
  - `result.status=partial` 且 `result.payload.outcome=submitted` 表示 relay 已提交 deterministic probe mail，但当前 runtime 不具备 observation lookup 能力，例如缺少 relay-visible `task_root`
  - `result.status=failed` 且 `result.payload.outcome=failed` 表示 relay 在 mail submission 阶段失败
  - `result.payload.observation` 当前会回告 projected PC observation summary 或 wait/skip state；operator 仍可直接读取 sidecar 做更底层对账
- accepted/replay continuity 当前直接复用 relay packet store：
  - 同一 `packet_id` replay 返回同一 `receipt_id`
  - 同一 bootstrap final result replay 返回同一 `result_id`
  - 同一 `transport_probe` replay 返回同一组 `event_id` / `result_id`
- `/control` 当前还不是 direct `new_task`、current-session direct `/status` / `reply`、Phase 3 detail subscribe 或 generic control payload 的通用替代

### 2.4 Current-Session Direct `/status` And Plain `reply`

- 当前 schema 是 `post-creation-session-action-contract-v1`
- 当前只支持两种 action：
  - `status`
  - `reply`
- 当前只支持 `target.scope = current_session`
- target identity 当前使用：
  - `workspace_id`
  - `session_id`
  - 可选 `thread_id`
- accepted path 是 bridge-to-mail，不是 direct terminal-result API：
  - relay 先接受 packet
  - 再把请求桥接进 canonical mail status/reply 路径
  - user-visible 结果仍由正常状态邮件或 terminal mail 给出

resolver 规则：

- 优先读取 `session_state`
- 若 relay 可见 task root 缺少对应 session 索引，允许回退到 `thread_state`
- 回退时优先使用请求显式提供的 `thread_id`
- 若未提供 `thread_id`，则按 `workspace_id/session_id` 扫描 `thread_state` 候选并补解析 canonical identity
- 若 identity 冲突或仍无法解析，则明确 reject，不做隐式猜测

`status` 当前边界：

- `task_run_packet.status` 必须是空对象
- bridge body 当前走 canonical `/status` 查询语义
- bridge body 会附带当前状态解析所需的 state capsule

plain `reply` 当前边界：

- 只支持普通自然语言 continuation
- 不支持 slash-command reply
- 不支持 structured answers
- 不支持 attachments
- 如果目标 session 当前是 `paused`，会 `validation_failed`
- 如果目标 session 当前处于 `awaiting_user_input`，会 `validation_failed`

### 2.5 Active-Session Detail Sidecar

- relay 当前仍可在显式 provision 后接受 `subscribe_session_detail`
- 这一路径仍是 read-side only
- 当前只用于一个 active session detail view 的 `session_snapshot` / `session_delta` 新鲜度
- 这一路径不改变 mail 作为 receipt/artifact/history truth 的角色

### 2.6 Relay `/v1/files` File Surface

- 当 `outbound_transport=relay`、存在 `relay_url + relay_transport_token`，且没有使用 COS external delivery 时，PC runtime 当前可把超阈值 artifact 上传到 relay host 的 `/v1/files`
- file-surface URL 当前由 `ws(s)://<relay-host>/relay` 派生为 `http(s)://<relay-host>/v1/files`
- 当前 schema version 是 `taskmail-control-artifact-contract-v1`
- 当前单文件上传上限是 `32 MiB`
- `/v1/files` 上传当前复用同一个 Bearer transport token
- 当前 live runtime 对 upload metadata 的 `kind` 只接受：
  - `image`
  - `file`
- `text` / `json` 当前还不是 live runtime 已接受的 `kind`
- 因此当前文本文件或 JSON sidecar 若走 `/v1/files`，应使用 `kind=file`，同时保留准确 `mime_type`
- local artifact truth 仍是 `RunArtifact` + `artifact_index.json`
- transport-facing `artifact_id -> file_id` 绑定不会污染 `artifact_index.json`，而是单独写入 `artifact_file_binding_index.json`
- user-visible 邮件当前仍保留 `Artifacts` 区域，并额外生成 `External Deliveries` 区域；超大文件不再继续作为 MIME 附件发送

- 该 Bearer transport token 当前也与 shared `/control` 复用同一认证路径
- Android 真机 debug smoke 已正向证明当前单样本闭环：
  - `POST /v1/files`
  - `GET /v1/files/{file_id}`
  - `GET /v1/files/{file_id}/content`
  - 本地 `sha256` 与回读内容一致
- 当前这份 Android 真机正向样本仍只覆盖 debug-only 单样本文本文件：
  - 首次 `kind=text` 被 live runtime 以 `invalid_metadata` 拒绝
  - 调整为 `kind=file` + `mime_type=text/plain; charset=utf-8` 后闭环
  - 不应把这轮样本误读成更宽文件类型矩阵已经完成验证
## 3. 当前不支持或仍保持 Mail-Only 的内容

以下内容当前不应误读成 direct current behavior：

- direct `/pause`
- direct `/resume`
- direct `/end`
- direct `/kill`
- direct `/last`
- direct `/sessions`
- `/control` 上的 direct `new_task`
- `/control` 上的 current-session direct `/status` / `reply`
- `/control` 上的 `subscribe_session_detail`
- cross-workspace direct session switching
- direct structured question answers
- attachment-bearing post-creation direct action
- 把 relay 当成完整 session/history/control API

## 4. 当前 Closeout / Evidence 落盘

### 4.1 Per-Run Canonical Summary

terminal status mail 发送后，runtime 当前会在：

- `tasks/<thread_id>/runs/<task_id>/canonical_summary.json`

落一份 per-run canonical summary。

当前最小字段集包括：

- `thread_id`
- `task_id`
- `run_status`
- `ingress_type`
- `ingress_message_id`
- `request_id`
- `packet_id`
- `receipt_id`
- `action_type`
- `target_session_identity`
- `last_summary`
- `terminal_mail_message_id`
- `terminal_mail_subject`
- `generated_at`

### 4.2 Session-Action Closeout

对 current-session direct `/status` 与 plain `reply`，runtime 当前会在：

- `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json`

落一份 thread-scoped session-action closeout。

当前最小字段集包括：

- `action_type`
- `target_session_identity`
- `request_id`
- `ingress_message_id`
- `packet_id`
- `receipt_id`
- `last_summary`
- `terminal_mail_message_id`
- `terminal_mail_subject`
- `generated_at`

### 4.3 Artifact File Binding Sidecar

对 `/v1/files` 上传，runtime 当前会在 artifact 根目录下写：

- `artifact_file_binding_index.json`

当前 schema 是 `taskmail-artifact-file-binding-index-v1`。

该 sidecar 的职责是：

- 保留 repo-local `artifact_id`
- 记录 transport-facing `file_id`
- 记录 `uploaded` / `failed` / `superseded` 绑定状态
- 保持本地 artifact truth 与 transport/file-plane 绑定分层

### 4.4 Transport-Probe Mailbox Observation Sidecar

当 PC host 从 bot mailbox 收到 `transport_probe` system mail 时，runtime 当前会在：

- `tasks/_mailbox/transport_probes/<probe_id>.json`

落一份 mailbox-level observation sidecar。

若 `probe_id` 含有不适合直接落文件名的字符，磁盘文件名会按百分号转义后的 `probe_id` 写入，但 sidecar 内仍保留原始 `probe_id`。

当前最小字段集包括：

- `schema_version`
- `probe_id`
- `request_id`
- `packet_id`
- `trace_id`
- `status`
- `observation_scope`
- `first_observed_at`
- `last_observed_at`
- `seen_count`
- `observed_message_ids`
- `delivery.transport_message_id`
- `delivery.subject`
- `delivery.from_addr`
- `delivery.to_addr`
- `delivery.mail_date`
- `probe_mail.schema_version`
- `probe_mail.scenario`
- `probe_mail.direction`
- `probe_mail.transport_kind`
- `probe_mail.payload_text`
- `probe_mail.timeout_seconds`
- `probe_mail.body_text`

该 sidecar 的当前职责是：

- 证明 PC mailbox loop 已实际收到 relay 注入的 deterministic probe mail
- 把 `probe_id/request_id/packet_id/trace_id` 与 bot mailbox 中的真实 `Message-ID` 对齐
- 为本地 authoritative task root 与 relay 可见 task-root sync 提供 operator-readable 证据
- 在 relay 具备 task-root 可见性时，为 `/control transport_probe_result.payload.observation` 提供同源 read-side evidence
- 不创建 thread/session，也不进入 canonical task mail 流转

### 4.5 Daily Closeout Bundle

当前可通过：

- `.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root <tasks>`

组装 `taskmail_daily_closeout_bundle.json`。

当前 bundle 会优先读取：

- `session_action_closeout.json`
- `canonical_summary.json`
- `thread_state.json`
- ingress / terminal raw mail
- outbound `delivery_attempts.jsonl`
- 可选 relay `packets.json` / `delivery_attempts.jsonl`

对 direct post-creation `reply` / `/status`，若存在 matching `session_action_closeout.json`，bundle 会优先把它读作 canonical outcome source。

## 5. 文档优先级

当 direct relay/control/file 相关文档发生冲突时，优先级如下：

1. 本文件
2. `docs/current/android_runner_communication_contract.md`
3. `docs/current/mail_protocol.md`
4. `docs/current/multimedia_mail_protocol.md`
5. `docs/current/pc_mail_output_protocol.md`
6. `docs/plans/*`

如果 `README.md` 或 `state.md` 与本文件冲突，以本文件为准。
