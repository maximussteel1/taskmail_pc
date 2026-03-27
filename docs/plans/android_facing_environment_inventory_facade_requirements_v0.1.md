# Android-Facing Environment Inventory Facade Requirements（v0.1）

更新时间：2026-03-27

## 状态

本文是 repo-side 对 `Android-facing environment inventory` 的 companion requirements。

截至 2026-03-27，first-pass 已经有对应代码落地。当前已冻结的实现边界是：

- 读取形态：HTTP `GET /v1/android/environment-inventory`
- 鉴权形态：Android app bearer token
- 返回形态：稳定 snapshot，而不是 push / stream

本文仍然主要回答“repo-side 应该承担哪些投影责任”，但上面这几个 first-pass 形态已经不再处于纯规划状态。

它依附于以下 Android 侧文档：

- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-environment-inventory-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-android-facing-facade-authority-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-page-api-requirements-v0.1.md`

它也依附于当前 repo-side 主线文档：

- `android_pc_vps_evolution_authority.md`
- `vps_first_multi_pc_control_plane_mainline_v0.1.md`
- `vps_first_multi_pc_phase1_execution_plan_v0.1.md`

本文不替代：

- `docs/current/*`
- `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`

这些文档分别回答“Android 页面怎么读”“repo-side 主线往哪走”“今天代码已经实现什么”“PC/VPS 内部如何说话”。
本文回答的是：

**repo-side 的 VPS / facade 应如何把内部 `pc_hello / workspace_snapshot` 与现有 inventory/store 真相层投影成 Android-facing `EnvironmentInventorySnapshot`。**

## 一句话结论

repo-side 应新增一条很薄的 Android-facing 环境库存读层：

- 输入是 repo-side 已有或应补齐的 `pc registration truth + workspace inventory truth + policy resolvers + missing-workspace backfill`
- 输出是 Android-facing 一份稳定的 `EnvironmentInventorySnapshot`

它的职责固定为：

- 返回 `pc -> workspace -> effective_execution_capabilities`
- 统一计算 `online / offline / unknown`
- 统一计算 `present / missing / stale`
- 统一给出 route admission

它不应要求 Android：

- 直接消费 raw `pc_hello / workspace_snapshot`
- 自己合并 capability
- 自己从 session 列表反推 `workspace missing`

## 目标

本文第一版只冻结以下 repo-side 责任：

1. 环境库存 facade 的输入真相层
2. 环境库存 facade 的投影流程
3. `pc` / `workspace` 稳定状态的计算责任
4. `effective_execution_capabilities` 的服务端合并责任
5. `workspace missing` 的 backfill 责任
6. Android-facing 返回中哪些字段必须稳定存在

本文第一版暂不冻结：

- Android 本地缓存格式
- inventory 增量推送协议
- 后续是否补 SSE / WebSocket 等实时读取形态
- 多租户复杂授权

## 设计原则

第一版 repo-side inventory facade 默认遵守以下原则：

1. facade 输出页面语义，不输出内部 runtime 协议细节。
2. `pc registration` 与 `workspace inventory` 是内部输入真相，不是 Android 直接读取对象。
3. inventory 与 session list 分层；两者可以 join，但不能混成同一 contract。
4. `effective_execution_capabilities` 必须在服务端先算好。
5. `workspace missing` 必须由 repo-side 主动建模，不能长期依赖 Android 临时兜底。
6. 单个 `pc` 或单个 workspace inventory 失败，不应把整个 inventory 读动作直接打成硬失败。
7. `stale / partial` 是正式返回语义，不是日志备注。

## 1. 输入真相层

repo-side 第一版环境库存 facade，最少应从下面四类输入真相层投影：

1. `pc registration truth`
2. `workspace inventory truth`
3. `execution capability / admission policy`
4. `missing-workspace backfill truth`

### 1.1 `pc registration truth`

最少应包含：

- `pc_id`
- `display_name`
- 最近一次有效 `pc_hello`
- 最近一次 heartbeat / live connection 观察
- `last_seen_at`
- 原始 `pc capabilities`

第一版推荐直接复用现有 Slice A store / runtime truth，不另外发明平行结构。

### 1.2 `workspace inventory truth`

最少应包含：

- `pc_id`
- `workspace_id`
- `repo_path`
- `workdir`
- `display_name`
- 最近一次 `workspace_snapshot`
- `last_snapshot_at`
- workspace 原始 capability 信息

第一版推荐直接复用现有 Slice B workspace inventory store。

### 1.3 `execution capability / admission policy`

repo-side 需要显式有一层 resolver，负责：

- 解析当前 `pc` 的 capability 上限
- 解析当前 workspace 是否有更细限制
- 计算某个 `pc/workspace` 当前是否允许新建 session

这层的输出不是内部 debug 对象，而是 Android-facing 的：

- `pc_capabilities`
- `workspace.effective_execution_capabilities`
- `route_admission`

### 1.4 `missing-workspace backfill truth`

repo-side 第一版必须允许从下列真相层回填 missing workspace：

- 已持久化的 session binding
- session projection / session store 中已知的 `workspace_id`
- 其他稳定的 workspace identity 索引

当前 first-pass 实现已经接通的是：

- `thread_bindings`
- `commands` 中可稳定读取的 `workspace_id / repo_path / workdir`

`session projection / session store` 作为更完整的 backfill 真相层仍属于后续增强，不应误读成“第一版代码已经全部接通”。

第一版不要求无限追历史，也不要求回填所有过期 workspace。

但至少要能覆盖：

- 仍被当前可见 session 引用的 `workspace_id`
- 刚刚因为 inventory 漂移而从 `present` 变成“最新 snapshot 不再出现”的 workspace

## 2. 投影总流程

repo-side 第一版建议固定以下投影顺序：

1. 读取当前可见 `pc registration truth`
2. 读取当前 workspace inventory store
3. 读取 capability / admission policy
4. 从 session 侧或 binding 侧回填 missing workspace
5. 生成统一 `EnvironmentInventorySnapshot`

当前 first-pass 代码里的对应读取顺序是：

1. `pc_control_runtime.list_nodes()`
2. `pc_control_runtime.list_workspaces()`
3. capability / admission 投影
4. `pc_control_runtime.list_thread_bindings()` 与 `pc_control_runtime.list_commands()` 回填 missing workspace
5. 输出 Android-facing snapshot

这条顺序的固定含义是：

- 先站在环境真相层
- 再补齐历史引用造成的 missing 节点
- 最后才形成 Android-facing 输出

而不是：

- 先按 Android 页面去猜节点
- 再回头补 store

## 3. `pc` 层投影责任

### 3.1 `pc.status`

repo-side 必须在 facade 内统一计算 `pc.status`：

- `online`
- `offline`
- `unknown`

固定责任：

- Android 不自己根据时间戳推断
- facade 根据 live connection / heartbeat / freshness policy 统一给出

第一版不冻结内部阈值，但冻结输出语义：

- `online`
  - 当前可认为具备 live route 能力
- `offline`
  - 当前无 live route，但仍可返回缓存 workspace
- `unknown`
  - 当前无法稳定判断

### 3.2 `workspace_inventory_state`

repo-side 还必须在 `pc` 层给出：

- `fresh`
- `stale`
- `missing`

固定责任：

- 这是该 `pc` 的 workspace inventory 质量读法
- 不是单个 workspace 的 `presence`

### 3.3 `pc_capabilities`

repo-side 必须把 `pc` 原始 capability 上报投影为稳定 Android-facing 字段：

- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- `backend_transport_modes`

第一版不允许 Android 自己从 raw `pc_hello` payload 做这层抽取。

### 3.4 `pc.route_admission`

repo-side 必须在 `pc` 层显式给出 route admission：

```json
{
  "allowed": false,
  "reason_code": "pc_offline",
  "reason": "Target PC is currently offline."
}
```

第一版建议稳定 `reason_code`：

- `pc_offline`
- `inventory_stale`
- `admission_blocked`
- `unknown`

## 4. `workspace` 层投影责任

### 4.1 `workspace.presence`

repo-side 必须显式计算：

- `present`
- `missing`
- `stale`

固定责任：

- `present`
  - 当前 workspace 出现在有效 inventory 中
- `missing`
  - 该 `workspace_id` 仍有正式 identity 价值，但已不在最新 inventory 中
- `stale`
  - 当前返回的是缓存 workspace 数据

### 4.2 `workspace missing` 的建模来源

第一版固定要求：

如果某个 `workspace_id` 满足以下任一条件，repo-side 应优先把它作为 `presence = missing` 正式返回：

1. 当前 session store / projection 里仍有可见 session 引用该 `workspace_id`
2. 最近的 binding / routed session 历史仍引用该 `workspace_id`
3. 该 workspace 刚从最新 inventory 中消失，但 repo-side 仍保有上一次有效 metadata

固定规则：

- `missing` 节点不是正常可创建目标
- 但它仍是首页树形和 session 归属关系的一等锚点

### 4.3 `effective_execution_capabilities`

repo-side 必须在服务端生成：

- `workspace.effective_execution_capabilities`

这层责任固定为：

1. 从 `pc` 原始 capability 出发
2. 应用 workspace 自身限制
3. 应用当前 admission / policy 限制
4. 产出 Android-facing 最终可读集合

第一版固定不允许：

- Android 自己拿 `pc_capabilities` 做二次交集或过滤

### 4.4 `workspace.route_admission`

repo-side 必须为每个 workspace 显式返回 route admission。

第一版建议稳定 `reason_code`：

- `workspace_unavailable`
- `pc_offline`
- `inventory_stale`
- `admission_blocked`
- `unknown`

固定规则：

1. `presence = missing` 时，默认 `allowed = false`
2. `presence = stale` 时，repo-side 可决定：
   - `allowed = false`
   - 或 `allowed = true` 但必须显式给出说明
3. 不允许把这些判断留给 Android 页面自己做

## 5. Android-facing 返回要求

repo-side facade 第一版至少要稳定返回以下顶层字段：

- `snapshot_id`
- `generated_at`
- `inventory_state`
- `refresh_after_seconds`
- `pcs`

并保证每个 `pc` 至少有：

- `pc_id`
- `display_name`
- `status`
- `last_seen_at`
- `workspace_inventory_state`
- `workspace_count`
- `pc_capabilities`
- `route_admission`
- `workspaces`

每个 `workspace` 至少有：

- `workspace_id`
- `pc_id`
- `display_name`
- `repo_path`
- `workdir`
- `presence`
- `last_snapshot_at`
- `effective_execution_capabilities`
- `route_admission`

## 6. `inventory_state` 的 repo-side 责任

repo-side 必须显式给出顶层：

- `fresh`
- `stale`
- `partial`

固定读法：

- `fresh`
  - 当前整体 inventory 适合作为页面主读层
- `stale`
  - 当前主要来自缓存，但仍有足够完整度
- `partial`
  - 当前只有部分 `pc/workspace` 成功投影

第一版不冻结内部判定算法，但建议遵守以下方向：

- 不因为单个 `pc` 暂时失败就让整个 inventory 变成硬错误
- 优先返回 `partial` 或 `stale`
- 只有在完全无可用数据时，才考虑上翻成请求失败

当前 first-pass 实现对 `partial` 采用的是更保守的页面语义：

- 只要出现 `unknown pc`
- 或出现 `missing workspace`

就会把整体 `inventory_state` 读成 `partial`。

这意味着当前代码还没有完整实现“单个 pc 投影失败但整体继续返回 partial”的隔离模型。该能力仍属于后续增强，不应和当前 `partial` 语义混淆。

## 7. 与内部协议的映射边界

repo-side environment inventory facade 应优先复用内部字段：

- `pc_id`
- `workspace_id`
- `display_name`
- `repo_path`
- `workdir`
- `supported_backends`
- `profile_catalogs`
- `permission_modes`
- `backend_transport_modes`
- `last_seen_at`
- `last_snapshot_at`

但第一版固定不应直接向 Android 暴露：

- `connection_epoch`
- `message_id`
- `trace_id`
- `host_fingerprint`
- `runtime_fingerprint`
- 原始 transport / fencing 细节

这些字段仍属于 repo-side 内部真相和调试层，而不是 Android-facing 产品 contract。

## 8. 初始实现顺序建议

repo-side 如果要按最小阻塞推进，建议顺序固定为：

1. 新增 `environment inventory projector`
2. 新增 `missing workspace backfill resolver`
3. 新增 Android-facing read endpoint 或 wrapper
4. 新增 focused tests
5. 最后再考虑 live push / 增量刷新

这样做的原因是：

- Android 首页和新任务页首先需要稳定 snapshot
- 不需要先等 live subscription 才能去掉占位符
- 两仓可以先围绕同一份 snapshot contract 并行实现

## 9. 测试要求

repo-side 第一版至少应覆盖以下 focused cases：

1. 单个在线 PC，多个 present workspace
2. 单个离线 PC，仍返回 stale workspace
3. workspace 从最新 inventory 消失，但因 session 引用被回填成 `missing`
4. workspace capability 比 pc capability 更窄
5. 单个 `pc` inventory 失败时，整体返回 `partial`
6. `pc_offline` 与 `workspace_unavailable` 不混淆

当前 first-pass 已优先覆盖：

- 在线 PC + present workspace
- 离线 PC + stale workspace
- missing workspace backfill
- workspace capability 比 pc capability 更窄
- `pc_offline` 与 `workspace_unavailable` 区分

第 5 条“单个 `pc` inventory 失败时整体返回 `partial`”仍对应后续更完整的隔离投影能力，不应误读成第一版已经完整覆盖。

## 10. 非目标

本文第一版明确不做：

- session list contract
- reply/status contract
- inventory push stream contract
- Android 本地缓存 contract
- 多租户 ACL 细化

这些都应继续拆分，不应塞进第一版 inventory facade 要求里。

## 一句话结论

**repo-side `environment inventory facade` 的 first-pass 要求只有一件事：把已有的 `pc registration + workspace inventory + policy + missing workspace backfill` 投影成 Android 可直接消费的一份稳定 snapshot，而不是让 Android 自己承担内部控制面的拼装和推断。**
