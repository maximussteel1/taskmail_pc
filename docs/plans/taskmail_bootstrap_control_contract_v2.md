# TaskMail Bootstrap Control Contract (v2)

## 状态

- 日期：2026-03-23
- 范围：第一份共享的 bootstrap direct roundtrip contract，当前仅覆盖 `[SYNC]` / `sync_project_folders`
- 层级：Layer 2 cross-repo contract freeze
- 相关文档：
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/plans/phase1_direct_connect_bootstrap.md`
  - `docs/plans/project_folder_sync_relay_single_account_plan.md`
  - `docs/plans/post_creation_session_action_contract_v1.md`

## 目的

冻结 Android 与 PC / VPS 在 `[SYNC]` 这条线上的最小 direct 双向协议，明确：

1. Android 发什么 request packet
2. 服务端先回什么 `packet_ack`
3. 服务端最终回什么 direct `bootstrap_result`
4. 失败时哪些情况允许 fallback 到当前 mail `[SYNC]`
5. replay / idempotency 应如何处理

这份文档当前**不**改写 `docs/current/*` 的 Layer 1 事实。
截至 2026-03-23，current behavior 仍然是：

- canonical `[SYNC]` 仍是 mail control-plane 动作
- 当前 direct bootstrap v1 仍按 bridge-to-mail 读取
- Android current contract 仍是 mail-first

本文件定义的是下一份 shared contract freeze，用于开始跨仓实现“direct request + direct result”的 `[SYNC]` roundtrip。

## 版本边界

`taskmail-bootstrap-control-contract-v1` 当前已经被 current docs 读作：

- Android 可把 `sync_project_folders` 发到 `/relay`
- relay 接受后桥接回 canonical `[SYNC]` mail ingress
- 最终结果仍由 mailbox 中的 `[SYNC] Project Folder List` 提供

因此，本轮**不重载** v1 语义，而是显式引入：

- `taskmail-bootstrap-control-contract-v2`

v2 的含义固定为：

- direct request
- direct `packet_ack`
- direct `bootstrap_result`
- accepted path 不再额外注入 canonical `[SYNC]` mail

这样可以避免 Android / PC / VPS 在同一个 `schema_version` 下读出两套相反语义。

## V2 范围

### In Scope

- `sync_project_folders` request packet
- `packet_ack.accepted=true` 的含义
- 最终 direct `bootstrap_result`
- fallback-classified rejection 与 hard rejection 的分类
- 同一 `packet_id` 的 replay / idempotency
- 与 current mail `[SYNC]` 路径并存时的边界

### Out Of Scope

- 其他 bootstrap action
- task / thread / session 创建
- `session_update`
- attachment / binary sync
- workspace summary API
- history API
- sender-account server-side 路由
- 把 `/relay` 升级成通用 app protocol

## Truth Boundary

`[SYNC]` 的执行真相仍然必须留在 PC 侧，而不是 VPS。

这意味着：

- relay / VPS 不能扫描 VPS 本地文件系统来代替 PC
- direct result 必须映射到当前 PC 侧 canonical helper 的业务语义
- repo-side canonical truth 仍然来自：
  - `mail_runner.project_folder_sync::list_project_folders(...)`
  - `mail_runner.project_folder_sync::build_project_folder_sync_body(...)`

accepted direct `[SYNC]` 也仍然必须满足：

- 不创建 task
- 不创建 runnable thread
- 不创建 session
- 不进入 task/session projection

## Transport Sequence

v2 的推荐时序如下：

1. Android 解析已保存的 relay config。
2. Android 连接 `/relay` 并完成 `hello -> hello_ack`。
3. Android 发送一个 `packet(sync_project_folders)`。
4. 服务端校验 payload，并把该请求路由到 PC 侧 bootstrap executor。
5. 若 direct path 成功得到结果，服务端返回 `packet_ack.accepted=true`。
6. 服务端随后返回且只返回一个 `bootstrap_result`。
7. 若同一 `packet_id` 被 replay，服务端必须返回同一 `receipt_id` 与同一结果语义。

为了降低 Android 侧状态机复杂度，v2 推荐：

- accepted path 在同一轮 handling 中就产出 `packet_ack + bootstrap_result`
- `packet_ack.accepted=true` 只在结果已经 materialized 或至少已 durably cached 到可 replay 时才允许返回

## Packet Wrapper

v2 继续复用现有 relay `packet` wrapper。

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:sync-project-folders:req_20260323_001",
  "client_trace_id": "req_20260323_001",
  "task_run_packet": {
    "schema_version": "taskmail-bootstrap-control-contract-v2",
    "action": "sync_project_folders",
    "request_id": "req_20260323_001",
    "origin": {
      "client": "android_taskmail",
      "sender_account_uuid": "acc_001"
    },
    "sync_project_folders": {}
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "taskmail-bootstrap-control-contract-v2",
    "action": "sync_project_folders",
    "fallback_policy": "mail"
  },
  "sent_at": "2026-03-23T15:00:00"
}
```

## 字段规则

### Wrapper

- `packet_id`
  - required
  - transport idempotency key
  - 推荐形状：`android-taskmail:sync-project-folders:<request_id>`
- `client_trace_id`
  - required
  - v2 中应与 `request_id` 相等
- `sent_at`
  - required
  - ISO timestamp

### `task_run_packet`

- `schema_version`
  - required
  - fixed to `taskmail-bootstrap-control-contract-v2`
- `action`
  - required
  - fixed to `sync_project_folders`
- `request_id`
  - required
  - 对应一次用户可见 direct `[SYNC]` 尝试的稳定 id
- `origin.client`
  - required
  - fixed to `android_taskmail`
- `origin.sender_account_uuid`
  - optional
  - 仅作 provenance
  - v2 不把它当作 server-side mailbox 路由主键

### `sync_project_folders`

- required
- v2 固定为空对象 `{}`
- v2 不支持在 request 中自定义 roots、filter 或 recursion

### `dispatch_metadata`

以下字段 required：

- `channel = taskmail_android_direct`
- `schema_version = taskmail-bootstrap-control-contract-v2`
- `action = sync_project_folders`
- `fallback_policy = mail`

`fallback_policy = mail` 在 v2 的含义是：

- pre-accept direct failure 仍允许 Android fallback 到 legacy mail `[SYNC]`
- accepted direct path 本身不再把结果写回 mailbox

## Ack 语义

accepted path 继续使用现有 `packet_ack`。

`packet_ack.accepted = true` 在 v2 中固定表示：

- 该 direct `[SYNC]` 请求已被接受进入 bootstrap direct lane
- 与该请求对应的 direct result 已经产生，或至少已持久化到足以支持 replay
- Android 现在应等待同一请求对应的 `bootstrap_result`

它**不**表示：

- canonical `[SYNC]` ingress mail 已经被发送
- mailbox 中已经存在 `[SYNC] Project Folder List`
- Android 应继续从邮箱读取这次 accepted direct `[SYNC]` 的最终结果

accepted path 约束：

- `receipt_id` required
- 对同一 `packet_id` 的 replay，`receipt_id` 必须稳定
- `transport_message_id` 在 v2 accepted path 中应省略；Android 不得把它当成 mailbox anchor

## Direct Result Message

v2 为 accepted path 新增独立的 `bootstrap_result` message。

```json
{
  "message_type": "bootstrap_result",
  "schema_version": "taskmail-bootstrap-control-contract-v2",
  "action": "sync_project_folders",
  "request_id": "req_20260323_001",
  "packet_id": "android-taskmail:sync-project-folders:req_20260323_001",
  "receipt_id": "rcpt_001",
  "result_id": "bootstrap-result:req_20260323_001",
  "sent_at": "2026-03-23T15:00:01",
  "sync_project_folders_result": {
    "summary_text": "Project folder sync completed. No task was created.",
    "scanned_at": "2026-03-23T15:00:01",
    "task_created": false,
    "thread_created": false,
    "session_created": false,
    "roots": [
      {
        "root_path": "E:\\projects",
        "available": true,
        "error": null,
        "entries": [
          {
            "name": "android_task_manager",
            "path": "E:\\projects\\android_task_manager"
          }
        ]
      },
      {
        "root_path": "D:\\projects",
        "available": false,
        "error": "path does not exist",
        "entries": []
      }
    ],
    "canonical_body_text": "Project folder sync completed. No task was created.\n\nScanned at: 2026-03-23T15:00:01\n..."
  }
}
```

## `bootstrap_result` 字段规则

### Common Fields

- `message_type`
  - required
  - fixed to `bootstrap_result`
- `schema_version`
  - required
  - fixed to `taskmail-bootstrap-control-contract-v2`
- `action`
  - required
  - fixed to `sync_project_folders`
- `request_id`
  - required
  - must equal request-side `request_id`
- `packet_id`
  - required
  - must equal request-side `packet_id`
- `receipt_id`
  - required
  - must equal ack-side `receipt_id`
- `result_id`
  - required
  - result identity for replay dedupe
  - 对同一 `packet_id` 的 replay 必须稳定
- `sent_at`
  - required
  - result emission timestamp

### `sync_project_folders_result`

- `summary_text`
  - required
  - human-readable one-line summary
- `scanned_at`
  - required
  - PC truth-side scan timestamp
- `task_created`
  - required
  - fixed to `false`
- `thread_created`
  - required
  - fixed to `false`
- `session_created`
  - required
  - fixed to `false`
- `roots`
  - required
  - ordered list aligned to configured project-root scan order
- `canonical_body_text`
  - required
  - plain-text parity payload
  - business meaning应与当前 canonical `[SYNC] Project Folder List` 正文一致

### `roots[*]`

- `root_path`
  - required
- `available`
  - required
  - boolean
- `error`
  - nullable
  - `available=false` 时应为 non-empty string
- `entries`
  - required
  - `available=false` 时固定为空数组

### `roots[*].entries[*]`

- `name`
  - required
- `path`
  - required

v2 继续沿用当前 `[SYNC]` 业务边界：

- 只列 configured roots
- 只列一级目录
- 不递归
- 不列普通文件

## Negative Path 与 Fallback 语义

v2 negative path 继续沿用现有 relay `error` frame，而不是发送 `bootstrap_result`。

推荐错误码分层如下：

- fallback-classified rejection
  - `unsupported_action`
  - `direct_temporarily_unavailable`
- hard rejection
  - `invalid_payload`
  - `validation_failed`
  - `unauthorized`

Android 行为冻结为：

- relay config 缺失、`hello_ack` 缺失、连接在 accepted 前中断
  - 可 fallback 到 legacy mail `[SYNC]`
- 收到 `unsupported_action` 或 `direct_temporarily_unavailable`
  - 可 fallback 到 legacy mail `[SYNC]`
- 收到 `invalid_payload` / `validation_failed` / `unauthorized`
  - 不应静默 fallback
  - 应保留本地错误与重试入口

## Replay / Idempotency

v2 明确要求：

- relay-level retry 或 reconnect retry 必须复用同一组 `packet_id + request_id`
- 只有 fresh user tap 才能生成新的 `packet_id + request_id`
- 同一 `packet_id` replay 必须返回：
  - 同一 `receipt_id`
  - 同一 `result_id`
  - 语义稳定的 `sync_project_folders_result`

accepted 之后的 Android 行为也要固定：

- 一旦已经收到 `packet_ack.accepted=true`，Android 不得自动发送一封 fresh mail `[SYNC]` 作为 silent fallback
- 如果 accepted 后连接丢失、但尚未拿到 `bootstrap_result`，Android 应先重连并 replay 同一 `packet_id`
- 如果 replay 仍无法恢复结果，应把它视为 direct-result recovery failure，并显式暴露错误，而不是偷偷切回 mail

## 与当前 mail `[SYNC]` 的并存边界

v2 在 rollout 期间允许与 current mail `[SYNC]` 并存，但边界必须清楚：

- legacy `[SYNC]` mail 仍保留为 fallback / manual path
- accepted direct v2 path 不再额外生成 `[SYNC] Project Folder List` mail
- current Layer 1 文档在代码、测试、closeout evidence 落地之前保持不变
- Android 不应把 direct result 混读成 task/session 数据源

这也意味着 earlier bridge-only 方案不再是 active direction。
`docs/plans/project_folder_sync_relay_single_account_plan.md` 应只保留为此前 alternative 的记录，而不是当前 shared contract。

## 当前结论

截至 2026-03-23，可以把 `[SYNC]` 这条 direct 双向协议先冻结为：

- request：`taskmail-bootstrap-control-contract-v2` `packet(sync_project_folders)`
- ack：现有 `packet_ack.accepted=true`
- result：新的 `bootstrap_result`
- accepted 后 truth：direct result，不再是 mailbox result
- rollout fallback：只在 accepted 之前或 fallback-classified rejection 时退回 legacy mail `[SYNC]`

后续仓库实现与 Android 实现都应以这份 v2 contract 为默认对齐目标，而不是继续把 `[SYNC]` 读作 bridge-to-mail only。
