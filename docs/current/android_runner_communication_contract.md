# Android Runner Communication Contract

> Document layer: Layer 1 (current Android app-facing contract)
>
> Current path: `docs/current/android_runner_communication_contract.md`

## 状态

- 日期：2026-03-29
- 目的：冻结当前 Android TaskMail 主链的 app-facing contract
- 范围：`/v1/android/*` 主链、idempotency、错误面、attachment direct contract，以及 `control_plane_mode=vps_only` 下的当前事实

## 1. 一句话契约

当前 Android 主链已经收口到 relay-only：

- Android 唯一正式 app-facing contract 是 `/v1/android/*`
- Android 主链不再发送业务 reply mail，也不再等待业务 reply mail
- create-session -> detail/read -> session-action -> result 的主链只依赖 relay-native app API
- 在 `control_plane_mode=vps_only` 下，Android 主链不再依赖 bot mailbox ingress、`canonical_reply_recipient` 或历史 inbound mail recovery

## 2. 当前正式入口

当前正式 Android app-facing surface 固定为：

- `POST /v1/android/create-session`
- `POST /v1/android/session-action`
- `GET /v1/android/sessions`
- `GET /v1/android/session-snapshot`
- `GET /v1/android/session-history`
- `WS /v1/android/session-updates`

这些入口统一使用：

- `Authorization: Bearer <android_app_token>`

它们不是 `/relay`、`/control`、`/debug/pc-control/*` 的 rebranding。

## 3. Create Session

`POST /v1/android/create-session` 当前是 Android 新建 session 的唯一正式入口。

当前最小业务字段：

- `pc_id`
- `workspace_id`
- `prompt`
- `execution_policy`

当前可选字段：

- `canonical_reply_recipient`
- `mode`
- `timeout_seconds`
- `acceptance`
- `attachments`
- `repo_path`
- `workdir`
- `source`

当前事实：

- `canonical_reply_recipient` 已降为可选字段，不再是 Android 主链前置依赖
- create-session 仍会把已提供的 `canonical_reply_recipient` 写入新 thread state；未提供时则保持为空
- 成功返回 `command_id + submit_ack`
- 当 `submit_ack.ack_status = accepted | accepted_but_queued` 时，同窗口还返回 `session_binding(session_id/pc_id/workspace_id)`

当前 facade-facing rejected `submit_ack.error_code` 固定收敛为：

- `unsupported_backend`
- `unsupported_profile`
- `unsupported_permission`
- `profile_model_unresolved`
- `workspace_unavailable`
- `pc_offline`

## 4. Session Action

`POST /v1/android/session-action` 当前是 Android current-session action 的唯一正式入口。

当前 action family 已一次收口到：

- `reply`
- `status`
- `pause`
- `resume`
- `kill`
- `end`
- `answers`
- `attachment_continuation`

当前 locator 最小要求：

- `target.session_id`

当前 supporting locator / 一致性校验字段：

- `target.workspace_id`
- `target.thread_id`

当前固定 body 规则：

- `pause` / `resume` / `kill` / `end` 使用空 object body
- `answers` 使用 `answers.question_answers[]`
- `attachment_continuation` 使用 `attachment_continuation.attachments[]`，并支持可选 `attachment_continuation.reply_text`

当前 session-action mainline 已切到 relay-native runtime execution：

- 不再桥接到 bot mailbox ingress
- 不再依赖 `canonical_reply_recipient`
- 不再依赖历史 inbound mail recovery
- `session_action_result` 现在表达 authoritative runtime result，而不是 `mail_ingress_submission`

当前 `session_action_result` 关键字段固定为：

- `result_scope = runtime_execution`
- `canonical_outcome_via = relay_runtime`
- `execution_status`
- `state_changed`
- `summary`
- `thread_status`
- `lifecycle`
- `current_task_id`
- `queued_task_id`
- `pending_question_ids`
- `run_result`
- `session_action_closeout`

## 5. Idempotency

当前 app-facing idempotency seam 固定收敛在 `session-action.request_id`：

- 同一 canonical payload 的 same-`request_id` replay 复用同一 submit response，并返回 `HTTP 200`
- 同一 `request_id` 命中不同 canonical payload 固定返回 `HTTP 409 request_id_conflict`

当前这条规则适用于整个已收口 action family：

- `reply/status/pause/resume/kill/end/answers/attachment_continuation`

## 6. 错误面

### 6.1 create-session rejected submit_ack

固定错误码：

- `unsupported_backend`
- `unsupported_profile`
- `unsupported_permission`
- `profile_model_unresolved`
- `workspace_unavailable`
- `pc_offline`

### 6.2 session-action rejected submit_ack

固定错误码：

- `direct_temporarily_unavailable`
- `invalid_command_payload`
- `pc_offline`
- `session_binding_unresolved`
- `session_identity_mismatch`
- `unsupported_backend`
- `unsupported_permission`
- `unsupported_profile`
- `validation_failed`
- `workspace_unavailable`

当前 `session_recipient_unresolved` 已退出 Android 主链 contract。

### 6.3 facade request / locator errors

当前常见 HTTP 错误包括：

- `400 invalid_json`
- `400 invalid_payload`
- `401 unauthorized`
- `404 session_not_found`
- `409 request_id_conflict`
- `409 session_binding_unresolved`
- `409 workspace_identity_mismatch`
- `503 pc_control_unavailable`
- `503 task_root_unavailable`
- `504 submit_ack_timeout`

## 7. Attachment Direct Contract

当前 Android app-facing inline attachment contract 固定为对象数组，不走 mail attachment ingress。

create-session `attachments[]` 与 `attachment_continuation.attachments[]` 的单项字段固定为：

- `name`
- `content_type`
- `content_bytes_b64`
- `size_bytes`（可选）

当前 `attachment_continuation` 的业务语义是：

- 服务器先把 inline attachment 物化到目标 workdir
- 然后直接进入 relay-native runtime continuation / answer execution
- 不再通过 attachment-bearing reply mail 做桥接

## 8. Authoritative Read Surface

当前 Android authoritative read surface 固定为三条：

- `GET /v1/android/session-snapshot`
- `GET /v1/android/session-history`
- `WS /v1/android/session-updates`

它们统一读取 relay-native projection store 中的 durable truth，并补充 `pc_control` ledger continuity。

其中：

- `session-snapshot` 面向 detail 当前态
- `session-history` 面向 durable history rounds
- `session-updates` 面向当前 detail 页的实时刷新

当前 detail 恢复语义固定为：

- Android 打开 detail 或回到前台时，连接 `WS /v1/android/session-updates`
- 连接成功后先消费一帧完整 snapshot
- 若连接断开、应用切后台后恢复，或客户端怀疑漏掉 push：
  - 重新连接 `session-updates`
  - 必要时调用 `GET /v1/android/session-snapshot` 做当前态补偿
- 这条 lane 当前不设计“客户端 ACK 后服务端停止 push”的协议
- 这条 lane 不再依赖 relay-visible `task_root` 镜像或 `sync companion`

## 9. vps_only 当前事实

在 `control_plane_mode=vps_only` 下，当前 Android 主链事实是：

- create-session 仍可创建 live session
- current-session action family 可完整工作
- snapshot / history / updates 可读 authoritative projection truth
- bot mailbox ingress 关闭时，Android 主链仍可完成 live smoke

## 10. 非目标

本文不再把以下路径视为 Android 主链：

- Android 业务 reply mail send path
- Android 业务 reply mail receive/wait path
- `/relay subscribe_session_detail`
- `/relay` 通用 WebSocket 协议
- `/control` 通用 WebSocket 协议
- `canonical_reply_recipient` / inbound raw mail recovery 作为 current-session 主链依赖
