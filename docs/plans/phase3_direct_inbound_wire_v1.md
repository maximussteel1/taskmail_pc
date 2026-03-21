# Phase 3 Direct Inbound Wire (v1)

## Status

- Date: 2026-03-21
- Scope: Phase 3 第一份 active-session detail direct inbound wire freeze
- Layer: Layer 2 cross-repo wire contract note
- Related docs:
  - `docs/plans/phase3_direct_inbound_mapping_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-next-session-handoff-2026-03-21-reply-direct-send-seam.md`

## Purpose

`phase3_direct_inbound_mapping_v1.md` 已经冻结了“Phase 3 第一刀要表达哪些业务事实”。

这份文档补齐上一份 mapping note 故意没写死的 wire 细节，只回答以下问题：

1. Android 如何在当前 `/relay` 上订阅一个 active session detail
2. server 应如何把 `session_snapshot` / `session_delta` 发给 Android
3. Android 遇到 gap、断连、重连、apply failure 时应如何重建

这不是 direct `reply`、direct `/status`、history API、workspace summary API 的合同。

## Current Coordination Note

截至 2026-03-21，这份 wire note 应视为下一次跨仓库实现的默认起点。

当前活跃顺序是：

1. 先冻结 active-session detail inbound wire
2. 再产出 fixture package
3. Android 先把它接入现有 session detail local-first seam

当前**不**把 direct `reply` / direct `/status` 作为下一轮活跃跨仓库实现目标。

## V1 Boundary

### In Scope

- `ws /relay` 上的 active-session detail 订阅
- accepted subscribe 之后的一次 `session_snapshot`
- 同 session 的 `session_delta`
- sequence / dedupe / resync 规则
- mail/direct coexistence 下的 direct read-side freshness

### Out Of Scope

- direct `reply`
- direct `/status`
- direct `/pause` / `/resume` / `/end`
- workspace summary direct contract
- history replay API
- attachment binary sync
- Android 专用 REST API

## Reuse Boundary

Phase 3 v1 沿用当前 relay 基线：

- 继续使用 public `ws /relay`
- 继续使用 token admission
- 继续使用 `hello -> hello_ack`
- 继续使用 `ping`
- 继续保留 mail fallback 与 mail receipt truth

Phase 3 v1 新增的是：

- 一个 client -> server 的 `subscribe_session_detail` packet
- 一个 server -> Android 的 `session_update` push message

## Connection Model

v1 为了降低排序与重建复杂度，冻结以下连接模型：

- 一个 WebSocket connection 在同一时刻只维护一个 active session detail subscription
- Android 若切换 detail 页面目标 session，应发送新的 subscribe request
- 新的 subscribe request 一旦被 accepted，旧 subscription 立即失效
- server 必须为每次 accepted subscribe 生成新的 `subscription_id`
- Android 必须丢弃不匹配当前 `subscription_id` 的后续 update

这条规则是 v1 的简化约束，不代表以后不能扩成多订阅。

## Message Set

Phase 3 v1 在当前 `/relay` 上新增并冻结以下消息集合：

1. client `packet` with `action = subscribe_session_detail`
2. server `packet_ack`
3. server `session_update`
4. existing `error`
5. existing `ping`

## Client Subscribe Packet

Android 在收到 `hello_ack` 之后，可发送一个 `packet` 来订阅当前 detail 页对应的 session。

### Wrapper

沿用现有 relay `packet` wrapper：

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:subscribe-detail:req_20260321_001",
  "client_trace_id": "req_20260321_001",
  "task_run_packet": {
    "schema_version": "phase3-direct-inbound-wire-v1",
    "action": "subscribe_session_detail",
    "request_id": "req_20260321_001",
    "origin": {
      "client": "android_taskmail"
    },
    "subscription": {
      "workspace_id": "workspace_a13f92d1c0ef",
      "repo_path": "E:\\projects\\android_task_manager",
      "workdir": "feature/taskmail/internal",
      "session_id": "session_001",
      "thread_id": "thread_001",
      "last_known_sequence": 42,
      "reason": "detail_open"
    }
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "phase3-direct-inbound-wire-v1",
    "action": "subscribe_session_detail"
  },
  "sent_at": "2026-03-21T18:00:00"
}
```

### Field Rules

- `packet_id`
  - required
  - transport idempotency key for this subscribe attempt
- `client_trace_id`
  - required
  - should equal `request_id`
- `task_run_packet.schema_version`
  - required
  - fixed to `phase3-direct-inbound-wire-v1`
- `task_run_packet.action`
  - required
  - fixed to `subscribe_session_detail`
- `task_run_packet.request_id`
  - required
  - stable per visible subscribe attempt
- `task_run_packet.origin.client`
  - required
  - fixed to `android_taskmail`
- `subscription.workspace_id`
  - optional but strongly recommended when Android 已保留 canonical `workspace_id`
- `subscription.repo_path`
  - optional fallback workspace locator when Android 当前 detail model 只有 repo path
- `subscription.workdir`
  - optional workspace disambiguator
  - if current detail 已有 canonical `workdir`，Android 应一并发送
  - normalization 应与当前 mail/state capsule 的 `workdir` 规则一致
- `subscription.session_id`
  - optional but strongly recommended when Android 已解析出 canonical `session_id`
- `subscription.thread_id`
  - optional fallback identifier when Android detail route 仍只有 `thread_id`
- `subscription.workspace_id` / `subscription.repo_path`
  - 至少要有一个存在
  - 如果两者同时存在，server 必须把它们解析到同一个 canonical workspace；否则应 reject
- `subscription.session_id` / `subscription.thread_id`
  - 至少要有一个存在
  - 如果两者同时存在，server 必须把它们解析到同一个 canonical session；否则应 reject
- `subscription.last_known_sequence`
  - optional positive integer
  - Android 最近一次成功应用到本地 direct projection 的 sequence
- `subscription.reason`
  - optional
  - allowed values in v1: `detail_open`, `detail_refresh`, `detail_reconnect`

### Business Meaning

`subscribe_session_detail` 的业务含义是：

- “我现在正在看这个 session detail”
- “如果你能安全恢复，可基于我给的 `last_known_sequence` 恢复”
- “如果不能安全恢复，请发新的 snapshot”
- “如果我只有 `repo_path + workdir`，请按当前 PC-side 规则先解析出 canonical `workspace_id` 再回给我”
- “如果我暂时只有 `thread_id`，请先解析出 canonical `session_id` 再回给我”

这不是 session ownership claim，也不是 direct control action。

v1 的 detail subscribe 仍然只覆盖“已经能定位到单个 active session”的 detail route。

这意味着：

- `workspace_id + session_id` 是首选路径
- `workspace_id + thread_id` 是已有 canonical workspace 情况下的 fallback 路径
- `repo_path + workdir + session_id` 是兼容 Android 当前 workspace id 丢失现实的 fallback 路径
- `repo_path + workdir + thread_id` 是最宽的兼容路径，但只有在 server 能唯一解析到 canonical workspace + session 时才应 accepted
- 如果只有 `repo_path` 而缺少可判定唯一 workspace 的 `workdir` / canonical workspace 线索，server 应 reject，而不是猜
- 连 `session_id` 与 `thread_id` 都无法确定的入口，仍属于 mail/local-cache only 范围，不在 v1 内

## Subscribe Ack

server 继续使用 `packet_ack` 响应 subscribe packet。

### `packet_ack.accepted = true`

表示：

- subscribe request 已被当前 relay/session handler 接受
- 该 connection 当前 subscription target 已切换到请求中的 session
- server 已解析出本次 subscription 使用的 canonical workspace / session identity
- server 接下来必须为该 subscription 发送至少一个 `session_update`

它**不**表示：

- Android 已收到 snapshot
- Android 已成功应用 snapshot
- mail read-side 可以被清空

如果 request 使用的是 `repo_path` / `workdir` fallback：

- server 后续 `session_update.workspace_id` 仍必须只发送 canonical `workspace_id`
- Android 应在本地缓存这个 canonical `workspace_id`，供后续 reconnect / refresh 优先复用

### `packet_ack.accepted = false`

推荐用于 subscription 级拒绝，例如：

- `session_not_found`
- `workspace_identity_unresolved`
- `workspace_identity_mismatch`
- `session_identity_unresolved`
- `subscription_rejected`
- `direct_temporarily_unavailable`

当 `accepted = false` 时：

- Android 保留当前 mail/local-cache detail
- Android 不应把它当成 mail fallback send path
- Android 可以稍后重试 subscribe，但不应清空 detail

### `error`

继续保留给 connection-level 或 malformed packet 错误，例如：

- `unauthorized`
- `invalid_json`
- `unsupported_message_type`
- `invalid_payload`

## Server Push Message

server -> Android 的 direct inbound update 在 v1 里冻结为单独的 `session_update` message。

```json
{
  "message_type": "session_update",
  "schema_version": "phase3-direct-inbound-wire-v1",
  "subscription_id": "sub_20260321_001",
  "workspace_id": "ws_repo_main",
  "session_id": "session_001",
  "thread_id": "thread_001",
  "task_id": "task_001",
  "update_id": "sessupd:session_001:43",
  "sequence": 43,
  "sent_at": "2026-03-21T18:00:03",
  "update_type": "session_snapshot",
  "session_snapshot": {}
}
```

### Common Fields

- `message_type`
  - required
  - fixed to `session_update`
- `schema_version`
  - required
  - fixed to `phase3-direct-inbound-wire-v1`
- `subscription_id`
  - required
  - identifies the currently accepted subscription on this connection
- `workspace_id`
  - required
- `session_id`
  - required
- `thread_id`
  - required
- `task_id`
  - required
- `update_id`
  - required
  - globally unique dedupe key
- `sequence`
  - required positive integer
  - monotonic within one `session_id`
- `sent_at`
  - required ISO timestamp
- `update_type`
  - required
  - `session_snapshot` or `session_delta`

## Ordering And Replay Rules

### Sequence

v1 冻结以下 sequence 规则：

- `sequence` 必须在单个 `session_id` 内严格单调递增
- Android 只能用 `sequence` 判断 direct update 新旧，不得用 wall-clock 替代
- server 不得在 reconnect 后把同一 `session_id` 的 sequence 重置回更小值
- 若 server 端 continuity 无法安全恢复，仍必须用更大的 sequence 发新的 snapshot，而不是回退到更小序号
- 若 server 因 transport retry 重发同一个 logical update，必须复用同一个 `update_id` 与同一个 `sequence`

### Snapshot First

accepted subscribe 之后：

- server 必须先发一个 `session_snapshot`
- 在该 snapshot 发出之前，不得先发 `session_delta`

### Gap Handling

Android 收到 `session_update` 后：

- 若 `subscription_id` 不匹配当前 active subscription，直接丢弃
- 若 `sequence` 小于等于当前已应用 sequence，按重复包丢弃
- 若 `sequence` 恰好等于当前已应用 sequence + 1，按正常路径处理
- 若 `sequence` 大于当前已应用 sequence + 1，视为 gap

当 Android 检测到 gap 时：

- 不应继续盲目应用后续 delta
- 应立即保留当前 mail/local-cache detail
- 应重新发送新的 `subscribe_session_detail` request，`reason = detail_refresh`

### Apply Failure

若 Android 因本地 schema 兼容性或状态机安全性原因无法应用某个 delta：

- 不应猜测修补该 delta
- 直接把当前 direct projection 标记为 dirty
- 重新 subscribe 同一 session，请求新 snapshot

### Server Resync Rule

server 在以下场景必须发新的 `session_snapshot`：

- accepted 新 subscribe 之后
- server 无法安全从 `last_known_sequence` 继续恢复时
- server 检测到当前 subscriber 所需的 delta 已不可安全补齐时
- server 自己决定用 snapshot 替换 delta 累积时

Android 必须把更新更“新”的 snapshot 当作当前 detail direct projection 的 authoritative rebuild base。

## `session_snapshot` Shape

`session_snapshot` payload 继续沿用 mapping note 的最小字段集：

```json
{
  "session_name": "Audit direct inbound wire",
  "backend": "codex",
  "repo_path": "E:\\projects\\android_task_manager",
  "workdir": "feature/taskmail/internal",
  "status": "running",
  "lifecycle": "active",
  "last_summary": "Running.",
  "last_active_at": "2026-03-21T18:00:02",
  "last_progress_at": "2026-03-21T18:00:02",
  "paused_from_status": null,
  "question_state": null,
  "timeline_items": []
}
```

### Field Rules

- `backend`
  - allowed values in v1: `codex`, `opencode`
- `status`
  - allowed values in v1:
    - `queued`
    - `running`
    - `awaiting_user_input`
    - `paused`
    - `done`
    - `failed`
    - `killed`
  - normalization rule:
    - current thread/scheduler `accepted` 应投影为 wire `queued`
    - current session `waiting_user` 应投影为 wire `awaiting_user_input`
- `lifecycle`
  - allowed values in v1:
    - `active`
    - `ended`
- `paused_from_status`
  - nullable
  - allowed values when present:
    - `queued`
    - `awaiting_user_input`
    - `done`
    - `failed`
    - `killed`
- `question_state`
  - nullable
  - when non-null it must represent the current canonical pending question set
  - must preserve current question capsule ordering and choice semantics
- `timeline_items`
  - required array
  - may be empty

## `question_state` Shape

`question_state = null` 表示当前没有 pending question set。

非空时最小 shape 为：

```json
{
  "question_set_id": "qset_001",
  "question_count": 2,
  "questions": [
    {
      "question_id": "q_1",
      "question_text": "Which branch should I use?",
      "question_type": "single_choice",
      "required": true,
      "choices": ["main", "release"],
      "choice_labels": {
        "main": "Main branch",
        "release": "Release branch"
      }
    },
    {
      "question_id": "q_2",
      "question_text": "Should I run the full test suite?",
      "question_type": "boolean",
      "required": true,
      "choices": ["yes", "no"],
      "choice_labels": {
        "yes": "Run full suite",
        "no": "Skip full suite"
      }
    }
  ]
}
```

字段约束：

- `question_set_id`
  - required
- `question_count`
  - required positive integer
  - should equal `questions.length`
- `questions`
  - required non-empty array
  - must preserve canonical pending question order
- `questions[*].question_id`
  - required
- `questions[*].question_text`
  - required
- `questions[*].question_type`
  - required
  - allowed values in v1:
    - `single_choice`
    - `boolean`
    - `short_text`
- `questions[*].required`
  - required boolean
- `questions[*].choices`
  - required array
  - `single_choice` / `boolean` 通常非空
  - `short_text` 可以为空
- `questions[*].choice_labels`
  - required
  - required object, may be empty
  - keys must be a subset of `choices`

Android 不应把这个 shape 当成新的 answer protocol。
它只是当前 canonical question-set / question-capsule 的 direct read-side projection。

## `timeline_items` Shape

v1 只冻结 detail 读侧真正需要的最小 item shape：

```json
{
  "item_id": "tl_001",
  "business_event_key": "reply/2026-03-21T18:00:02",
  "item_type": "assistant_reply_preview",
  "created_at": "2026-03-21T18:00:02",
  "status": null,
  "text": "I am checking the current direct-connect seam.",
  "question_set_id": null,
  "question_ids": [],
  "paused_from_status": null
}
```

### Common Fields

- `item_id`
  - required
  - stable direct-source dedupe key inside the session timeline
- `business_event_key`
  - required
  - stable cross-source reconciliation key
  - same business event projected到 direct 与 mail 时必须共用同一 key
- `item_type`
  - required
  - allowed values:
    - `status_transition`
    - `assistant_reply_preview`
    - `question_prompt`
    - `paused_hint`
    - `terminal_summary`
- `created_at`
  - required ISO timestamp
- `status`
  - nullable
  - used by `status_transition` and `terminal_summary`
- `text`
  - nullable
- `question_set_id`
  - nullable
- `question_ids`
  - required array
- `paused_from_status`
  - nullable

### Recommended `business_event_key` Families

v1 不把这个字段做成 opaque “随便填”，而是冻结以下语义族：

- `status/<status>/<event_ts>`
- `reply/<event_ts>`
- `question/<question_set_id>/<event_ts>`
- `paused/<paused_from_status>/<event_ts>`
- `terminal/<status>/<event_ts>`

其中：

- `event_ts` 应来自能被后续 mail 投影复用的同一 canonical business-event 时间戳
- 如果 server 暂时拿不到稳定的 canonical event basis，就不应伪造一个会让 mail/direct 无法对账的 key

### Item-Specific Expectations

- `status_transition`
  - `status` 应存在
  - `business_event_key` 应使用 `status/<status>/<event_ts>` 语义族
- `question_prompt`
  - `question_set_id` 应存在
  - `question_ids` 应存在
  - `text` 应存在
  - `business_event_key` 应使用 `question/<question_set_id>/<event_ts>` 语义族
- `paused_hint`
  - `paused_from_status` 应存在
  - `text` 应存在
  - `business_event_key` 应使用 `paused/<paused_from_status>/<event_ts>` 语义族
- `terminal_summary`
  - `status` 应存在
  - `text` 应存在
  - `business_event_key` 应使用 `terminal/<status>/<event_ts>` 语义族
- `assistant_reply_preview`
  - `text` 应存在
  - `business_event_key` 应使用 `reply/<event_ts>` 语义族

### Append Rule

v1 不支持对已有 timeline item 做 patch update。

这意味着：

- `timeline_append` 只能追加新 item
- Android 按 `item_id` 去重
- server 不应依赖“改写已有 `assistant_reply_preview` 文本”来驱动 UI
- 若 server 不能安全只靠 append 表达当前 detail state，应直接发新 snapshot
- direct timeline item 是 provisional overlay，不是 durable history
- Android 在 direct/mail merge 时必须先按 `business_event_key` 做跨源对账，再决定是否 append 到最终 detail timeline

## `session_delta` Shape

```json
{
  "delta_type": "state_transition",
  "state_transition": {
    "status": "awaiting_user_input",
    "lifecycle": "active",
    "last_summary": "Need user input.",
    "last_active_at": "2026-03-21T18:01:00",
    "last_progress_at": "2026-03-21T18:01:00",
    "paused_from_status": null,
    "question_state": {
      "question_set_id": "qset_001",
      "question_count": 1,
      "questions": [
        {
          "question_id": "q_1",
          "question_text": "Should I continue with repo A or repo B?",
          "question_type": "single_choice",
          "required": true,
          "choices": ["repo_a", "repo_b"],
          "choice_labels": {
            "repo_a": "Continue with repo A",
            "repo_b": "Continue with repo B"
          }
        }
      ]
    }
  }
}
```

### Allowed Delta Types

- `state_transition`
- `timeline_append`

### `state_transition`

最小字段：

- `status`
- `lifecycle`
- `last_summary`
- `last_active_at`
- `last_progress_at`
- `paused_from_status`
- `question_state`

其中：

- `status` required
- 其他字段在 v1 中允许 nullable，但语义上应表达“当前最新状态投影”

### `timeline_append`

最小字段：

- `timeline_items`

字段约束：

- `timeline_items` required non-empty array
- 每个 item 必须满足上面的 `timeline_items` shape

## Freshness And Coexistence Rule

v1 不定义一个“mail 与 direct 的全局统一比较器”。

相反，authority 按字段族固定：

- direct authority:
  - 当前活跃 session 的 header status
  - 当前活跃 session 的 `last_summary`
  - 当前活跃 session 的 `question_state`
  - 当前活跃 session 的 lightweight timeline projection
- mail authority:
  - terminal receipt 正文
  - attachment metadata
  - attachment binary
  - artifact section
  - external deliveries
  - current html/plain rich-text fidelity

跨源对账规则也在 v1 一并冻结：

- direct timeline item 先按 `item_id` 做 direct-source dedupe
- Android 在 mail/direct merge 时必须再按 `business_event_key` 做 cross-source reconciliation
- 如果 mail item 与 direct item 命中同一 `business_event_key`，mail item 必须胜出，direct item 必须被 suppress / replace，而不是双重显示
- Android 不一定要把 `business_event_key` 持久放进最终 UI model，但 merge 层必须先消费它

特别规则：

- direct terminal update 可以先把 detail header 切到 `done` / `failed` / `killed`
- mail terminal receipt 一旦到达，正文/附件/artifact 仍以 mail 为准
- mail `[DONE]` / `[FAILED]` / `[KILLED]` receipt 一旦到达，matching `terminal_summary` direct item 必须被 suppress
- mail `[QUESTION]` receipt 一旦到达，matching `question_prompt` direct item 必须被 suppress
- mail `[PAUSED]` receipt 一旦到达，matching `paused_hint` direct item 必须被 suppress
- mail durable assistant reply 一旦到达，matching `assistant_reply_preview` direct item 必须被 suppress
- 若 direct 与 mail 在业务含义上冲突，应记 mismatch，不应让 Android 猜测调和

## Disconnect Rule

当 direct connection 断开、stale、或当前 subscription 不可恢复时：

- Android 不应清空 detail
- Android 继续显示最近可用的 mail/local-cache detail
- direct 失效不应破坏 draft、reply anchor、attachment selection
- Android 可以在 detail 仍可见的前提下后台重试 subscribe

## Recommended First Fixture Package

配合本 wire note，下一份共享工件应至少提供以下 fixtures：

1. subscribe accepted -> snapshot(`queued`, from current accepted/queued work)
2. subscribe accepted -> snapshot(`running`, no assistant output)
3. subscribe accepted -> snapshot(`running`, with assistant output preview)
4. snapshot(`awaiting_user_input`, single question with canonical choices/labels)
5. snapshot(`awaiting_user_input`, multi-question set)
6. snapshot(`paused` from `awaiting_user_input`)
7. snapshot(`done`)
8. snapshot(`failed`)
9. snapshot(`killed`)
10. snapshot + delta(`state_transition`)
11. snapshot + delta(`timeline_append`)
12. direct `question_prompt` / `paused_hint` / `terminal_summary` later suppressed by matching mail item
13. gap detected -> Android resubscribe -> fresh snapshot

每个 fixture 至少应包含：

- one subscribe request
- one `packet_ack`
- one or more `session_update`
- expected Android projection note
- expected coexistence note versus latest mail-derived state

## Current Conclusion

Phase 3 下一轮跨仓库实现现在应读作：

- 不是继续扩 direct send-side actions
- 而是先把 active-session detail 的 inbound wire freeze 写死

下一次默认实现起点应是：

1. 仓库侧按本 note 落地 parser / emitter / fixture producer
2. Android 侧按本 note 落地 subscribe consumer / local-first detail adapter
3. direct `reply` / direct `/status` 留到后续独立 scope note
