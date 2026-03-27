# Android Session Snapshot Facade Contract

> Document layer: Layer 1 (current app-facing read contract)
>
> Current path: `docs/current/android_session_snapshot_facade_contract.md`

## 状态

- 日期：2026-03-27
- 目的：冻结当前 repo-side `GET /v1/android/session-snapshot` 的已实现行为
- 范围：locator、鉴权、错误面，以及当前 first-pass `session summary + session_snapshot` 返回形态
- 补充：`session_snapshot.history_rounds` 的 round-level 字段与排序规则，另见 `android_session_history_rounds_contract.md`

## 1. 一句话契约

当前 repo-side 已提供一个 Android-facing 的单 session detail 读接口：

- `GET /v1/android/session-snapshot`
- 鉴权：`Authorization: Bearer <android_app_token>`
- 真相层：relay 可见 `task_root` 下的 `SessionState + ThreadState`

这条 seam 的目标不是暴露 phase3 wire message，而是给 Android 一个可直接消费的单 session 快照。

## 2. Locator 规则

当前 first-pass 支持的 locator 字段是：

- `workspace_id`
- `repo_path`
- `workdir`
- `session_id`
- `thread_id`

当前最小命中规则：

- `thread_id` 单独即可
- `session_id` 不能单独使用
- `session_id` 需要配合以下任一条件：
  - `workspace_id`
  - `repo_path + workdir`
  - `thread_id`

当前 repo-side 不会对 `session_id` 做跨 workspace 猜测。

## 3. 顶层返回

当前成功返回固定包含：

- `schema_version`
- `snapshot_id`
- `generated_at`
- `locator`
- `session`
- `session_snapshot`

其中：

- `session` 是 Android-facing summary，字段口径与 `GET /v1/android/sessions` 对齐
- `session_snapshot` 是当前 first-pass detail projection

## 4. `locator` 字段

当前 `locator` 固定返回 canonical：

- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`

其中 `pc_id` 不是 task-root 原生字段，而是 repo-side 的保守投影。

当前 `pc_id` 解析顺序固定为：

1. `thread_bindings`
2. `command history`
3. 当前 `workspace inventory` 中的唯一命中

若仍不能唯一确定，则返回 `pc_id = null`。

## 5. `session` 字段

当前 `session` 直接复用 session-list summary 字段集，至少包含：

- `session_id`
- `thread_id`
- `pc_id`
- `workspace_id`
- `session_name`
- `status`
- `lifecycle`
- `backend`
- `backend_transport`
- `profile`
- `permission`
- `repo_path`
- `workdir`
- `current_task_id`
- `queued_task_id`
- `pending_task_count`
- `last_summary`
- `last_active_at`
- `last_progress_at`
- `backend_session_id`
- `backend_session_resumable`
- `created_at`
- `updated_at`

注意：

- 当前这里的 `status` 直接沿用 `SessionState.status`
- 因此它可能仍是 `waiting_user`

## 6. `session_snapshot` 字段

当前 `session_snapshot` 是 repo-side detail projection，至少包含：

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
- `question_state`
- `timeline_items`
- `history_rounds`

这里的 `status` 不是原始 `SessionState.status`，而是当前 detail 投影使用的 normalized wire status。

当前 first-pass 下，常见映射包括：

- `queued`
- `running`
- `awaiting_user_input`
- `paused`
- `done`
- `failed`
- `killed`

例如：

- `SessionState.status = waiting_user`
- `session_snapshot.status = awaiting_user_input`

### 6.1 `history_rounds`

当前 `history_rounds` 是挂在 `session_snapshot` 下的增量历史快照字段。

它的职责是：

- 给 Android 历史复盘页提供按回合组织的 durable snapshot

它当前不是：

- 独立 endpoint
- 完整 timeline replay
- output chunk transcript

`history_rounds` 的字段集、排序和回退语义，统一以：

- `docs/current/android_session_history_rounds_contract.md`

为准。

## 7. 错误面

当前错误返回包括：

- Android app token 不匹配：`401 unauthorized`
- relay 未配置 `task_root`：`503 task_root_unavailable`
- locator 缺失或结构不合法：`400 invalid_payload`
- session 无法命中：`404 session_not_found`
- locator 与 canonical identity 冲突：`409 workspace_identity_mismatch` 或其他同类 identity conflict

当前这些错误都不会被 repo-side 自动降级成“猜一个最近 session”。

## 8. 非目标

本文不定义：

- session delta / subscribe push
- timeline replay / full history
- artifact download
- post-creation action facade

当前 contract 只覆盖 Android-facing 的单 session snapshot first pass。
