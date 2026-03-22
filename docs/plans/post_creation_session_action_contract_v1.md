# Post-Creation Session-Action Contract (v1)

## Status

- Date: 2026-03-22
- Scope: first shared contract freeze for direct post-creation session actions
- Layer: Layer 2 cross-repo contract freeze
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase2_direct_outbound_contract_v1.md`
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`

## Purpose

冻结 direct `new_task` 之后的第一批 post-creation session action 合同，使 Android 与 PC / VPS 可以在**不改写当前
Layer 1 事实**的前提下开始实现 planning。

这份文档当前只回答：

1. 第一批 direct post-creation action 到底包含什么
2. 这些 action 的 target identity、bridge 语义、fallback 语义怎么冻结
3. accepted 之后是否仍沿当前 canonical mail truth layer 产出结果
4. 如果后续进入 live closeout，最少应保留哪些 machine-readable anchors

这份文档当前**不**意味着：

- `docs/current/*` 已经把 direct `reply` / direct `/status` 读成 current protocol
- Android 已经应该把 session detail UI 接到 direct control seam
- Phase 4 的 covered flow 已默认扩到 `reply` / `/status`

## Current Reading

截至 2026-03-22，仓库侧 current reading 仍然是：

- Layer 1 仍是 mail-first control plane
- 当前 direct 例外仍只包括：
  - `new_task`
  - active-session-detail read-side sidecar
- direct `reply` / direct `/status` 现在仍不属于 current protocol
- 当前 canonical truth layer 仍是 current mail truth layer
- 当前 same-workspace targeted command routing 是 mail control-plane 能力，不自动授权 direct targeted-session variant

因此，这份文档是**新的 shared contract freeze**，不是对 `docs/current/*` 的追认。

## Ownership Boundary

当前 ownership 先明确冻结为：

- 不扩写 `phase2-direct-outbound-contract-v1`
- 另起一份 `post-creation session-action contract`
- 在该 contract 落地实现并完成 closeout 之前，不直接写进 `docs/current/*`

更准确地说：

- `phase2_direct_outbound_contract_v1.md`
  - 继续只负责 first-slice `new_task`
- `post_creation_session_action_contract_v1.md`
  - 负责 first-slice post-creation actions
- `docs/current/mail_protocol.md` / `docs/current/android_runner_communication_contract.md`
  - 继续只描述当前已实现事实

## V1 Scope

### In Scope

本合同 v1 只冻结两类 direct action：

- `current-session plain reply`
- `current-session /status`

### Out Of Scope

本合同 v1 明确不冻结：

- quick answer
- multi-question `Answers:`
- paused `/resume`
- attachment continuation
- targeted-session variant
- cross-workspace switching
- direct `/pause`
- direct `/resume`
- direct `/end`
- direct `/kill`
- direct `/last`
- direct `/sessions`

### Scope Reading

如果一个 direct packet 试图把上述 out-of-scope 行为塞进 v1，应按本合同的 `hard_stop` 口径处理，而不是由 server 静默
扩义。

## Reuse Boundary

本合同 v1 沿用当前 relay transport wrapper 与 current canonical mail semantics：

- 继续使用 `/relay`
- 继续使用 token admission
- 继续使用 `hello -> hello_ack`
- 继续使用 `packet -> packet_ack`
- 继续保留 mail fallback
- 继续保留 current canonical mail truth layer

本合同 v1 不引入新的 Android-facing REST API，也不把 direct wire 升级成新的 general-purpose app protocol。

accepted direct action 的 business meaning 当前固定为：

- Android 只声明 one current canonical session target 与当前 user intent
- server side 负责把该动作桥接进现有 canonical mail-side action ingress
- 后续 user-visible outcome 仍按 current canonical mail contract 产出

## Packet Wrapper

本合同 v1 继续沿用现有 relay `packet` wrapper。

### Direct `reply` Example

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:session-action:req_20260322_001",
  "client_trace_id": "req_20260322_001",
  "task_run_packet": {
    "schema_version": "post-creation-session-action-contract-v1",
    "action": "reply",
    "request_id": "req_20260322_001",
    "origin": {
      "client": "android_taskmail"
    },
    "target": {
      "scope": "current_session",
      "workspace_id": "ws_repo_main",
      "session_id": "thread_041",
      "thread_id": "thread_041"
    },
    "reply": {
      "reply_text": "Please continue and keep the current scope."
    }
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "post-creation-session-action-contract-v1",
    "action": "reply",
    "fallback_policy": "mail"
  },
  "sent_at": "2026-03-22T23:00:00"
}
```

### Direct `/status` Example

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:session-action:req_20260322_002",
  "client_trace_id": "req_20260322_002",
  "task_run_packet": {
    "schema_version": "post-creation-session-action-contract-v1",
    "action": "status",
    "request_id": "req_20260322_002",
    "origin": {
      "client": "android_taskmail"
    },
    "target": {
      "scope": "current_session",
      "workspace_id": "ws_repo_main",
      "session_id": "thread_041",
      "thread_id": "thread_041"
    },
    "status": {}
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "post-creation-session-action-contract-v1",
    "action": "status",
    "fallback_policy": "mail"
  },
  "sent_at": "2026-03-22T23:00:05"
}
```

## Field Rules

### Wrapper Fields

- `packet_id`
  - required
  - transport idempotency key
- `client_trace_id`
  - required
  - should equal `request_id`
- `sent_at`
  - required
  - ISO timestamp

### `task_run_packet` Envelope

- `schema_version`
  - required
  - fixed to `post-creation-session-action-contract-v1`
- `action`
  - required
  - allowed values in v1:
    - `reply`
    - `status`
- `request_id`
  - required
  - stable per visible direct-send attempt
- `origin.client`
  - required
  - fixed to `android_taskmail`

### `target`

- `target.scope`
  - required
  - fixed to `current_session`
- `target.workspace_id`
  - required
  - canonical workspace identity
- `target.session_id`
  - required
  - canonical session identity
- `target.thread_id`
  - optional supporting identity
  - if present, server must verify that it resolves to the same canonical thread as `workspace_id + session_id`

v1 当前不接受：

- `repo_path + workdir` fallback target locating
- targeted-session variant
- cross-workspace switching

如果 Android 当前 detail route 仍拿不到 canonical `workspace_id + session_id`，则不应走本合同 v1 的 direct path。

### `reply`

- `reply.reply_text`
  - required
  - non-empty plain-text payload
  - business meaning 对齐 current plain reply continuation

v1 reply 当前不接受：

- attachment-bearing payload
- multi-question `Answers:`
- quick answer specialization
- paused `/resume`
- command multiplexing
- current-session plain reply 之外的控制动作混入同一 packet

### `status`

- `status`
  - required
  - fixed to empty object `{}` in v1

v1 status 当前不接受：

- targeted-session status
- cross-workspace status
- current-session `/status` 之外的额外 control payload

### `dispatch_metadata`

required fields:

- `channel = taskmail_android_direct`
- `schema_version = post-creation-session-action-contract-v1`
- `action = reply | status`
- `fallback_policy = mail`

## Business Meaning Freeze

### Direct `reply`

`reply` 的 accepted 业务含义固定为：

- server 已按 `workspace_id + session_id` 解析出 canonical current session
- Android packet 只声明 current canonical session target
- server side 负责桥接进现有 canonical mail reply ingress
- Android 不负责构造 mail `In-Reply-To` / `References` 等价物
- 后续 run / question / paused / terminal receipt 仍沿 current canonical mail truth layer 产出

`reply` 的 accepted **不**表示：

- backend run 已经开始
- terminal status mail 已存在
- Android 可以绕过 current mail truth layer 自己推断最终 outcome

### Direct `/status`

`status` 的 accepted 业务含义固定为：

- server 已按 `workspace_id + session_id` 解析出 canonical current session
- server side 负责桥接进现有 canonical `/status` ingress 语义
- 后续仍产出 canonical `[STATUS]` mail
- `Subject`、state capsule、reply-visible summary / terminal semantics 与 mail path 保持一致

`status` 的 accepted **不**表示：

- Android 可以直接把 ack 当成最终状态结果
- current canonical `[STATUS]` mail 可以省略
- targeted-session `/status` 已被当前合同授权

## Waiting / Paused Boundary

为避免 v1 偷偷吸收更复杂的 reply semantics，本合同当前固定：

- direct `reply` 只覆盖 normal current-session plain reply continuation
- 如果目标 session 当前需要 quick answer、multi-question `Answers:`、paused `/resume` 或 attachment continuation：
  - server 必须 reject
  - 不得把该请求静默改写成其他 action

这不是说这些语义以后不会 direct 化，而是它们不属于本合同 v1。

## Ack Meaning

### `packet_ack.accepted = true`

表示：

- 该 direct post-creation action 已进入当前 direct lane
- target canonical session identity 已被成功解析
- server 已接受该次 bridge request

它不表示：

- canonical mail outcome 已经送达
- terminal summary 已可见
- Android 可以不再等待 canonical mail truth layer

### `packet_ack.accepted = false`

推荐用于 action-level rejection，例如：

- `unsupported_action`
- `direct_temporarily_unavailable`
- `session_identity_unresolved`
- `session_identity_mismatch`
- `current_session_only_violation`
- `paused_resume_not_supported`
- `answer_flow_not_supported`

### `error`

继续保留给 connection-level 或 malformed packet 错误，例如：

- `unauthorized`
- `invalid_json`
- `invalid_payload`
- `validation_failed`

## Machine-Readable Classification

本合同 v1 至少冻结以下三档读法。

### `fallback_required`

适用情形：

- relay config missing
- `hello` 未到达 `hello_ack`
- connection drops before `packet_ack`
- `unsupported_action`
- `direct_temporarily_unavailable`

期望行为：

- Android 回退到 canonical mail current-session action path
- `reply` 回退到 canonical reply mail
- `/status` 回退到 canonical mail `/status`
- 不得伪装成 direct accepted

### `hard_stop`

适用情形：

- `invalid_payload`
- `validation_failed`
- `unauthorized`
- `session_identity_unresolved`
- `session_identity_mismatch`
- `current_session_only_violation`
- `reply` packet 混入 quick answer / `Answers:` / paused `/resume` / attachment continuation
- `status` packet 试图请求 targeted-session variant

期望行为：

- 不静默 fallback
- 保留 draft 或当前 UI state
- 明确显示 direct-send error

### `switch_blocker`

这不是单纯的 ack-level error code，而是 parity / closeout severity。

只要发生以下任一情况，就应按 `switch_blocker` 读：

- accepted direct `reply` 无法收敛到 canonical mail reply outcome
- accepted direct `/status` 未产生 canonical `[STATUS]` mail
- subject / state capsule / terminal semantics 与 mail path 漂移
- accepted 样本缺失足够 closeout anchors，导致 same-run bind 不能稳定成立

## Closeout Artifacts

如果后续进入 live closeout，本合同 v1 至少规划以下 artifact 字段：

- `action_type`
  - allowed values in v1:
    - `reply`
    - `status`
- `target_session_identity`
  - `workspace_id`
  - `session_id`
  - optional `thread_id`
- `request_id`
- `ingress_message_id`
- `terminal_mail_subject`
- `last_summary`
- `same_run_bind`

当前建议的 supporting anchors 还包括：

- optional `packet_id`
- optional `terminal_mail_message_id`
- optional `transport_message_id`

### Same-Run Bind Reading

本合同 v1 对 closeout 的最低要求不是发明新的 bind ladder，而是继续保留：

- `request_id`-first 读法
- supporting `ingress_message_id`
- supporting `terminal_mail_subject`
- supporting `last_summary`

`same_run_bind` 至少应能表达：

- `effective_bind_level`
- `matched_fields`
- `mismatched_fields`
- `strong_bind`

## Explicit Non-Goals

本合同 v1 当前不做：

- 把 `TaskSessionDetailViewModel` 接到 direct control seam
- 要求 Android 猜 packet schema 之外的 hidden bridge behavior
- 把 quick answer / `Answers:` / `/resume` / attachment continuation 偷偷并进第一批
- 代替 `docs/current/*` 宣告 direct `reply` / direct `/status` 已是 current protocol
- 自动扩大到 targeted-session 或 cross-workspace switching

## Current Conclusion

截至 2026-03-22，仓库侧与 Android 侧可以把 post-creation direct action 的第一份 shared contract 冻结为：

- ownership 与 `phase2_direct_outbound_contract_v1.md` 分离
- v1 scope 仅包含 current-session plain reply 与 current-session `/status`
- target boundary 固定为 `current_session only`
- target identity 以 `workspace_id + session_id` 为主，`thread_id` 仅作 supporting identity
- accepted 之后继续沿 current canonical mail truth layer 产出结果
- machine-readable classification 与 closeout artifact 最低集合已明确

在这份 contract 有实现与 closeout evidence 之前，`docs/current/*` 仍应保持现状，不提前改写 Layer 1 事实。
