# Android Sessions Facade Contract

> Document layer: Layer 1 (current app-facing read contract)
>
> Current path: `docs/current/android_sessions_facade_contract.md`

## 状态

- 日期：2026-03-27
- 目的：冻结当前 repo-side `GET /v1/android/sessions` 的已实现行为
- 范围：鉴权、默认过滤、返回形态，以及当前 first-pass `pc_id` 解析语义

## 1. 一句话契约

当前 repo-side 已提供一个 Android-facing 会话列表读接口：

- `GET /v1/android/sessions`
- 鉴权：`Authorization: Bearer <android_app_token>`
- 真相层：relay-native projection store 中的 `projection_sessions`

它当前是一个薄读投影，不直接暴露内部 mail thread 文件结构，也不要求 Android 自己从 command / workspace / binding 拼 session 列表。

## 2. 顶层返回

当前返回固定包含：

- `schema_version`
- `snapshot_id`
- `generated_at`
- `refresh_after_seconds`
- `session_count`
- `sessions`

当前 `sessions` 是一个 snapshot 列表，不是流式增量协议。

## 3. 默认过滤语义

当前 first-pass 默认只返回：

- `lifecycle=active` 的会话

如果 Android 需要把 ended 会话也拉出来，必须显式带：

- `include_ended=true`

当前还支持的只读过滤参数：

- `pc_id`
- `workspace_id`
- `session_id`
- `thread_id`

这些过滤都是“与后”的收窄过滤，不会改变其他字段语义。

## 4. Session 层字段

每个 `session` 当前至少包含：

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

当前 `status` 直接沿用 `SessionState.status`，第一版不额外重命名或重新归一化。

## 5. `pc_id` 解析语义

当前 first-pass 的 `pc_id` 不是 task-root 原生字段，而是 repo-side 的保守投影结果。

当前解析顺序固定为：

1. `thread_bindings`
2. `command history`
3. 当前 `workspace inventory` 中的唯一命中

也就是说：

- 若 thread binding 已能唯一定位 `pc_id`，直接采用 binding truth
- 若 binding 缺失，则尝试用 `session_id + workspace_id` 在 command history 中唯一定位
- 若 command history 也不能唯一定位，则尝试用当前在线或已知 inventory 中该 `workspace_id` 的唯一 PC 命中
- 若仍无法唯一确定，则返回 `pc_id = null`

当前 contract 明确不做“猜测式归属”，尤其不会在多 PC 同时拥有同一 `workspace_id` 时随意挑一个。

## 6. 错误面

当前接口在以下情况下会直接报错而不是返回空列表：

- Android app token 不匹配：`401 unauthorized`
- relay 未配置或不可用 projection store：`503 task_root_unavailable`（兼容错误码）

当前若 projection store 已配置但暂时没有 session 数据，则返回 `200` + 空列表。

## 7. 非目标

本文不定义：

- session detail / snapshot contract
- timeline / replay / delta push
- artifact download
- reply / status / pause / resume 等 post-creation action facade

当前 contract 只覆盖 Android-facing 的 session list first pass。
