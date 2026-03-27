# Android Environment Inventory Facade Contract

> Document layer: Layer 1 (current app-facing read contract)
>
> Current path: `docs/current/android_environment_inventory_facade_contract.md`

## 状态

- 日期：2026-03-27
- 目的：冻结当前 repo-side `GET /v1/android/environment-inventory` 的已实现行为
- 范围：鉴权、返回形态、`pc/workspace` 投影字段，以及当前 first-pass `route_admission` 语义

## 1. 一句话契约

当前 repo-side 已提供一个 Android-facing 环境库存读接口：

- `GET /v1/android/environment-inventory`
- 鉴权：`Authorization: Bearer <android_app_token>`
- 返回：稳定 snapshot，而不是内部 `pc-control` 原始协议

它当前负责把内部：

- `pc registration truth`
- `workspace inventory truth`
- `effective execution capabilities`
- `missing workspace backfill`

投影成 Android 可直接消费的：

- `inventory_state`
- `pc.status`
- `workspace.presence`
- `pc/workspace.route_admission`

## 2. 顶层返回

当前返回固定包含：

- `schema_version`
- `snapshot_id`
- `generated_at`
- `inventory_state`
- `refresh_after_seconds`
- `pcs`

当前 `inventory_state` 语义：

- `fresh`
- `stale`
- `partial`

当前 first-pass 下：

- 出现 `missing workspace` 时，整体会读成 `partial`
- 出现 `unknown pc` 时，整体会读成 `partial`
- 仅出现 `offline pc` / `stale workspace` 时，整体可读成 `stale`

## 3. PC 层字段

每个 `pc` 当前至少包含：

- `pc_id`
- `display_name`
- `status`
- `last_seen_at`
- `workspace_inventory_state`
- `workspace_count`
- `pc_capabilities`
- `route_admission`
- `workspaces`

当前 `pc.status` 只输出：

- `online`
- `offline`
- `unknown`

当前 `pc.route_admission` 是 Android-facing 的页面语义，不是内部 dispatch trace。  
第一版当前已落地的稳定 reason code 为：

- `pc_offline`
- `unknown`
- `workspace_unavailable`
- `unsupported_backend`
- `admission_blocked`

当前判定规则：

- `pc.status=offline` 时，返回 `pc_offline`
- `pc.status=unknown` 时，返回 `unknown`
- 只要任一 workspace `route_admission.allowed=true`，则 `pc.route_admission.allowed=true`
- 没有任何 workspace 时，返回 `workspace_unavailable`
- 所有 workspace 都是 `missing` 时，返回 `workspace_unavailable`
- 所有 workspace 都因为没有可用 backend 而不可路由时，返回 `unsupported_backend`
- 其余不可路由组合，返回 `admission_blocked`

## 4. Workspace 层字段

每个 `workspace` 当前至少包含：

- `workspace_id`
- `pc_id`
- `display_name`
- `repo_path`
- `workdir`
- `presence`
- `last_snapshot_at`
- `effective_execution_capabilities`
- `route_admission`

当前 `presence` 只输出：

- `present`
- `missing`
- `stale`

当前 `workspace.route_admission` 第一版已落地的稳定 reason code 为：

- `workspace_unavailable`
- `pc_offline`
- `unsupported_backend`
- `unknown`

当前判定规则：

- `presence=missing` 时，返回 `workspace_unavailable`
- `pc.status=offline` 时，返回 `pc_offline`
- `pc.status=unknown` 时，返回 `unknown`
- 当前 `effective_execution_capabilities.supported_backends=[]` 时，返回 `unsupported_backend`
- 其余情况下，返回 `allowed=true`

## 5. Missing Workspace Backfill

当前 first-pass 已接通的 missing workspace 回填真相层为：

- `thread_bindings`
- `commands`

当前回填优先级：

1. 先用 `thread_bindings` 回填 canonical `workspace_id / repo_path / workdir`
2. 再用 `commands` 补 `last_snapshot_at`
3. 若只有 command history，可退化为 command-only backfill

因此当前行为固定为：

- `missing workspace` 是正式返回节点，不是 Android 本地占位推断
- 当 binding 与 command history 冲突时，以 binding identity 为准

## 6. 非目标

本文不定义：

- Android 本地缓存格式
- 增量推送协议
- session list / timeline / replay contract
- 内部 `pc-control` message 级别字段

当前 contract 只覆盖 Android-facing 环境库存 snapshot。
