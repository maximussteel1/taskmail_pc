# Android Session Snapshot Facade Contract

> 文档层级：Layer 1（current app-facing read contract）
>
> 当前路径：`docs/current/android_session_snapshot_facade_contract.md`

## 状态

- 日期：2026-03-29
- 目的：冻结当前 `GET /v1/android/session-snapshot` 的已实现行为
- 范围：locator、返回字段、`latest_session_action` authoritative continuity，以及它与 `session-history` / `session-updates` 的关系

## 1. 一句话契约

当前 repo-side 已提供 Android-facing 的单 session detail 快照：

- `GET /v1/android/session-snapshot`

它是 Android detail 页的 authoritative read surface 之一，不暴露 phase3 wire message 本身。

## 2. 鉴权与真相层

- 鉴权：`Authorization: Bearer <android_app_token>`
- 真相层：relay-native projection store 中的 session projection + history projection
- continuity 补充来源：`pc_control` command ledger

## 3. Locator 规则

当前支持的 locator 字段：

- `workspace_id`
- `repo_path`
- `workdir`
- `session_id`
- `thread_id`

当前最小命中规则：

- `thread_id` 单独即可
- `session_id` 单独也可，但前提是 repo-side 能唯一命中一个 canonical session
- `workspace_id`、`repo_path + workdir`、`thread_id` 在 `session_id` 路径下都视为 supporting locator / 一致性校验

当前 repo-side 不会对多命中的 `session_id` 猜测最近 session；歧义固定返回 `409 session_binding_unresolved`。

## 4. 成功返回

当前成功返回固定包含：

- `schema_version`
- `snapshot_id`
- `generated_at`
- `locator`
- `session`
- `session_snapshot`

其中：

- `locator` 返回 canonical `pc_id/workspace_id/session_id/thread_id`
- `session` 是 Android-facing summary
- `session_snapshot` 是 detail projection

## 5. `session_snapshot` 字段

当前 `session_snapshot` 至少包含：

- `session_name`
- `backend`
- `repo_path`
- `workdir`
- `status`
- `lifecycle`
- `last_summary`
- `last_active_at`
- `last_progress_at`
- `paused_from_status`
- `live_process`
- `question_state`
- `timeline_items`
- `latest_session_action`
- `history_rounds`

这里的 `status` 是 detail projection 的 normalized status，不直接等同于 raw `SessionState.status`。

当前 `live_process` 的 contract 固定为：

- wire shape 固定是 `object | null`
- 当前 repo-side 即使没有 live process，也返回 `"live_process": null`，而不是省略字段
- 首轮 Android-facing 字段固定包含：
  - `status`
  - `updated_at`
  - `items`
- `items[]` 与 `history_rounds[].process.items[]` 使用同一套 canonical process item schema：
  - `item_id`
  - `kind`
  - `created_at`
  - `updated_at`
  - `status`
  - `text`
- 它表达的是当前 session 的聚合 live process 投影，不等于 raw `output_chunk[]` transcript

当前 `last_progress_at` 还补充一条 build-time 规则：

- detail 对外值取 `max(session.last_progress_at, live_process.updated_at)`
- repo-side 不会为此回写 session row，只在 snapshot build 时动态合成

## 6. `latest_session_action`

`session_snapshot.latest_session_action` 当前是可选 continuity projection。

当 repo-side 已能从 `pc_control` ledger 归属出当前 session 最近一条：

- `reply`
- `status`
- `pause`
- `resume`
- `kill`
- `end`
- `answers`
- `attachment_continuation`

时，会返回 object；否则返回 `null`。

当前 continuity object 最少包含：

- `command_id`
- `action_type`
- `submit_ack`
- `created_at`
- `acked_at`
- `pc_id`

当 ledger 里已经记录到该 command 的 authoritative `session_action_result` 时，还会增量返回：

- `result_status`
- `session_action_result`

当前 `session_action_result` 应读为 relay-native runtime result：

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

固定边界：

1. `latest_session_action` 来源于 `pc_control` ledger，不是 task-root 原生字段。
2. 它当前只服务于 Android current-session action family continuity。
3. `submit_ack` 不是最终业务结果；authoritative business result 以 `session_action_result` 与新的 `session_snapshot` 投影为准。

## 7. `history_rounds`

`session_snapshot.history_rounds` 仍保留在 detail payload 内，供 detail 页直接读取最近历史。

同时，当前 repo-side 也已提供独立：

- `GET /v1/android/session-history`

两者共用同一 round projector，字段语义以：

- `docs/current/android_session_history_rounds_contract.md`

为准。

补充约束：

- `history_rounds[].result.attachments[].download_ref` 现在允许返回 VPS `/v1/files` 对应的 canonical object
- Android 应把它读作“当前 detail / history 页可消费的文件动作入口”，而不是 public share URL
- `history_rounds[].input.attachments` 仍不承诺稳定下载引用

## 8. 与 `session-updates` 的关系

当前 repo-side 还提供：

- `WS /v1/android/session-updates`

它推送的 `session_snapshot` message payload 与本接口成功返回 payload 保持同构。

也就是说：

- HTTP `session-snapshot` 是点查
- WS `session-updates` 是同构 payload 的增量推送

当前 Android 恢复语义固定为：

- `session-updates` 负责持有中的 detail 实时刷新
- `session-snapshot` 负责冷启动、重连后补偿、以及怀疑漏消息时的 authoritative 当前态回读
- 两者使用同一套 locator；Android 不需要为 detail lane 再接 legacy `/relay subscribe_session_detail`

## 9. 错误面

当前错误返回包括：

- `401 unauthorized`
- `503 task_root_unavailable`（projection store 不可用时的兼容错误码）
- `400 invalid_payload`
- `404 session_not_found`
- `409 session_binding_unresolved`
- `409 workspace_identity_mismatch`
- `409 session_identity_mismatch`

当前 repo-side 不会把这些错误自动降级成“猜一个最近 session”。
