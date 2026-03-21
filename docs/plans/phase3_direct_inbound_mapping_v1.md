# Phase 3 Direct Inbound Mapping (v1)

## Status

- Date: 2026-03-21
- Scope: Phase 3 第一份 direct inbound read-side mapping 冻结，面向 Android TaskMail 现有 session detail UI
- Layer: Layer 2 cross-repo mapping note
- Related docs:
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase2_direct_outbound_closeout_handoff.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/pc_mail_output_protocol.md`
  - `docs/current/session_scheduler_status.md`
  - `E:\projects\android_task_manager\docs\TASKMAIL-ANDROID-CURRENT-STATUS.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-public-plaintext-direct-connect-plan-v0.1.md`

## Purpose

冻结 Phase 3 的第一刀，不再停留在“以后 direct read-side 要怎么做”的泛化讨论。

这份文档只回答两个问题：

1. PC / VPS 第一批 direct inbound update 应该表达哪些现有 TaskMail 业务事实
2. Android 应该怎样把这些事实接进现有 local-first session detail 读侧

这不是完整 app API 设计，也不是 Phase 3 全量终局。

## Current Reading

当前可以作为固定前提的事实是：

- Phase 2 direct `new_task` v1 已经闭环，accepted / fallback / hard rejection 都已完成 live 验证
- 当前用户可见 status / result / attachment truth 仍然来自 mail
- PC 仍然是 task-execution truth
- Android 侧已经有可复用的 local-first seam：
  - `UnifiedMessage` cache
  - local-first workspace / session detail loading
  - incremental upsert
  - incremental-first detail snapshot rebuild with full-rebuild fallback
- Android 当前尚未把 workspace summary projection 收口到和 detail snapshot 同样稳定的状态

因此，Phase 3 第一刀不应该先做“大而全的 direct read API”，而应该先做“当前活跃 session detail 的 direct 补强层”。

## V1 Boundary

### In Scope

- 一个当前活跃 session 的 direct read-side snapshot
- 同一 session 的增量 update
- 当前高信号状态语义：
  - `queued`
  - `running`
  - `awaiting_user_input`
  - `paused`
  - `done`
  - `failed`
  - `killed`
- 当前 session detail 所需的最小 timeline 投影
- mail-derived 与 direct-derived 状态的共存规则

### Out Of Scope

- workspace list / workspace summary 的 direct 主路径替换
- 全量 history API
- 附件二进制同步
- direct reply / direct control action
- 新的 Android 专用 REST API
- 把 mail read-side 提前删除

## Reuse Boundary

Phase 3 v1 应继续复用当前 direct-connect 基线，而不是重新发明第二条接入面：

- 沿用当前 public `ws /relay`
- 沿用 token admission
- 沿用当前 connection lifecycle
- mail 继续存在，且继续承担 fallback 与对账职责

这一阶段新增的是“server -> Android 的 direct update 语义”，不是新的任务业务语义。

## Mapping Principle

所有 direct inbound update 都必须能回译到当前 mail control plane 已经存在的业务事实。

换句话说，direct inbound 不是第二套协议世界，而是以下现有 truth 的另一种投影：

- state capsule
- question capsule
- `Summary:`
- `Reply:`
- `Artifacts:` / `Attachment Notices:` 的存在性边界
- `lifecycle`
- `last_active_at`
- `last_progress_at`

如果某个 direct 字段无法明确映射回这些现有事实，就不应进入 v1。

## Common Identity And Ordering Fields

无论 snapshot 还是 delta，v1 都应至少带上这些字段：

- `schema_version = phase3-direct-inbound-mapping-v1`
- `workspace_id`
- `session_id`
- `thread_id`
- `task_id`
- `update_id`
- `sequence`
- `sent_at`

字段约束：

- `update_id` 必须全局可去重
- `sequence` 必须在单个 `session_id` 内单调递增
- Android 不应依赖 wall-clock 排序替代 `sequence`
- Android 必须允许“收到新 snapshot 后丢弃旧 delta 累积结果并重建当前 detail state”
- `workspace_id` 仍是 server -> Android update 的 canonical workspace identity
- Android 在 subscribe 阶段若暂时缺失 `workspace_id`，可按 wire note 使用 `repo_path` / `workdir` fallback 让 server 解析；但一旦收到 direct update，后续应优先复用 canonical `workspace_id`

## Direct Update Types

### 1. `session_snapshot`

`session_snapshot` 是 v1 的起点。

它代表“当前这个 session detail 页面，Android 立刻需要知道的完整最小状态”。

建议最小字段：

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

其中：

- `question_state` 应表达当前 canonical pending question set 的最小可投影信息，至少保留 `question_id`、`question_text`、`question_type`、`required`、`choices`、`choice_labels`
- `timeline_items` 只要求覆盖 detail 页面当前可见的核心事件，不要求变成全量历史导出

### 2. `session_delta`

`session_delta` 是在 snapshot 之后到来的同 session 增量变化。

v1 只建议支持两类 delta：

- `state_transition`
- `timeline_append`

不要在第一刀里引入复杂 patch 语言。

Android 侧允许的最小消费方式应该是：

- 若能安全应用，就增量更新当前 detail snapshot
- 若不能安全应用，就等待下一次 `session_snapshot` 重建

## Session Status Mapping

### `queued`

direct `queued` 在 v1 里承担当前 accepted / queued 可见状态的统一投影：

- current thread / scheduler `accepted` 应在 wire 层归一到 `queued`
- Android detail header 可展示 queued/accepted 语义
- 不应伪造 `running` 才会出现的 assistant output preview

### `running`

direct `running` 语义必须等价于当前 mail / state truth 的“正在跑”：

- detail header 展示运行中状态
- `last_summary` 可继续作为顶部摘要
- 如果当前有最新 assistant-visible 输出，可作为 detail timeline 的最新增量内容
- 如果当前还没有 assistant-visible 输出，也不能伪造回复文本

### `awaiting_user_input`

direct `awaiting_user_input` 必须保持与当前 `[QUESTION]` 语义一致：

- 单题仍可映射到 quick answer 能力
- 多题仍映射到 structured answers 能力
- direct 不得绕开当前 canonical question-set / question-id 约束
- `question_text` / `question_type` / `required` / `choices` / `choice_labels` 必须与当前 question capsule 语义保持一致

### `paused`

direct `paused` 必须保留当前 mail 语义：

- plain reply 不应被视为恢复
- Android detail 仍应要求显式 `/resume`
- `paused_from_status` 如果存在，应该被保留给 detail 层使用

### `done` / `failed` / `killed`

direct terminal 状态在 v1 里只承担“快速读侧更新”职责，不替换 mail receipt truth：

- Android 可以立即把 detail header 切到终态
- `last_summary` 和当前 terminal preview 可以先更新
- 完整 terminal receipt、附件、artifact 列表、external deliveries 仍以 mail 为准

## Timeline Mapping Boundary

v1 timeline 不要求把当前邮件正文完整复刻为一套新的 direct 文档模型。

第一刀应只覆盖 detail 读侧真正高价值的最小集合：

- status transition item
- latest assistant-visible reply text
- question prompt item
- paused hint item
- terminal summary item

以下内容继续留在 mail truth 层：

- 完整 artifact section
- attachment notice 的逐项细节
- inline image / attachment 二进制内容
- 完整 HTML fragment fidelity

## Coexistence Rules

### Rule 1: Mail remains durable receipt truth

以下内容在 v1 里仍以 mail 为主：

- terminal receipt 正文
- attachment metadata 与二进制内容
- external deliveries
- current rich-text fragment fidelity

### Rule 2: Direct may override current detail freshness

以下内容在 Android detail 上可以优先吃 direct，只要 direct 更新更“新”：

- `queued`
- `running`
- `awaiting_user_input`
- `paused`
- `done` / `failed` / `killed` 的 header-level early state flip
- 当前活跃 session 的最新 summary
- 当前活跃 session 的 `question_state`
- 当前活跃 session 的最新 assistant-visible output preview

### Rule 3: Timeline items are provisional overlays

v1 direct timeline item 不是 durable history，而是 detail 读侧的 provisional overlay：

- Android 不应把 direct timeline merge 理解成“无条件 append”
- direct-source 内部先按 `item_id` 去重
- direct 与 mail 之间再按 `business_event_key` 做对账
- 如果同一 business event 的 mail durable item 已到达，direct item 必须被 suppress / replace，而不是双重显示
- Android 不一定要把 `business_event_key` 暴露到最终 UI model，但 merge 层必须先消费它

### Rule 4: Disconnect must degrade safely

当 direct 连接断开、stale、或 update 不可应用时：

- Android 不应清空 detail
- Android 应退回到当前 mail-derived / local-cache-derived detail 视图
- direct state 失效不应破坏当前 draft、attachment selection、reply anchor

### Rule 5: No hidden semantic fork

如果 direct 与 mail 在业务语义上冲突：

- 先视为实现问题
- 记录 mismatch
- 不应靠“Android 额外猜测”去调和两套不同 meaning

## Recommended First Fixture Set

Phase 3 第一轮 fixtures 至少应覆盖：

1. `queued`，包含 current `accepted -> queued` 的 wire 归一投影
2. `running`，且还没有 assistant output
3. `running`，且已有 assistant output preview
4. `awaiting_user_input` with single question
5. `awaiting_user_input` with multi-question set
6. `paused` from `awaiting_user_input`
7. `done`
8. `failed`
9. `killed`
10. same business event 的 direct item 被后续 mail durable item suppress / replace

每个 fixture 都应至少提供：

- one `session_snapshot`
- zero or more `session_delta`
- expected Android detail projection notes
- expected coexistence behavior versus the latest mail-derived state

## Recommended Implementation Order

1. 先冻结 `phase3_direct_inbound_wire_v1`
2. 再冻结 `session_snapshot` / `session_delta` 的最小 shape
3. 先只让 PC / VPS 发 active-session detail 所需字段
4. Android 先只把它接入 session detail 页面
5. 跑通 running / question / paused / done / failed
6. 再决定 workspace summary 是继续聚合还是引入 dedicated summary snapshot

## Explicit Non-Goals

这份文档当前不冻结：

- workspace summary direct contract
- direct attachment transport
- direct reply / control action contract
- packet history read API
- Android 侧 dedicated lifecycle / health UI

这些都应该在后续 scope note 里单独讨论，而不是挤进 v1。

## Current Conclusion

Phase 3 v1 现在应被理解为：

- 不是“direct 替代 mail”
- 而是“先把 active session detail 的 read-side freshness 从 mail-only 提升到 mail + direct coexistence”

下一次实现会话的默认起点应该是：

- 仓库侧先产出 `phase3_direct_inbound_wire_v1`
- 仓库侧产出 `session_snapshot` / `session_delta` 最小 shape
- Android 侧把它接进现有 session detail local-first seam

workspace summary、history、attachment、reply/control 都不应阻塞这一刀。
