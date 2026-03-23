# TaskMail `/control` Repo-Side 实现责任说明（v0.1）

更新时间：2026-03-23

## 状态

- 本文是 `mail_based_task_manager` 仓对 vNext `/control` control plane 的 repo-side 最小实现责任说明。
- 本文不改写 `docs/current/*` current truth；current Android-facing direct boundary 仍是 `/relay` 及其现有 phase contract。
- 本文的作用是把 companion note 中“repo-side 已承认的 `/control` 基线”进一步压成实现责任，而不是继续停留在抽象口号。

## Read First

- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-communication-development-conditions-v0.1.md`
- `docs/current/android_runner_communication_contract.md`
- `docs/plans/vps_relay_bootstrap_plan.md`
- `docs/plans/taskmail_bootstrap_control_contract_v2.md`
- `docs/plans/taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `docs/plans/taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`
- `mail_runner/relay_server/app.py`
- `mail_runner/relay_server/protocol.py`
- `mail_runner/relay_server/packet_store.py`

## 1. 一句话责任

`/control` 在 repo-side 的首版责任是：

- 提供单一、带认证的 control ingress
- 对进入的 control request 做协议级 admission
- 在 `accepted = true` 前先拿到 durable replay authority
- 对 Android 暴露稳定的 `command_ack / event / result / error` 语义

它不是新的业务真相层；业务执行真相仍在 PC runtime。

## 2. 本文覆盖什么

本文只覆盖 repo-side 最小实现责任：

- `/control` endpoint 的 admission、认证与握手责任
- shared message set 在 repo-side 的最低语义要求
- accepted/replay 的 repo-side 分责
- 当前代码资产如何作为 vNext `/control` 的实现起点
- merge 前应具备的最小证据

本文不覆盖：

- 具体业务 payload 字段定义
- Android UI 或 ViewModel 逻辑
- token 轮换与过期策略细则
- `/v1/files` 的上传线协议细节

## 3. 与 current `/relay` 的关系

repo-side 当前必须同时坚持两件事：

- `docs/current/*` 的 current direct boundary 仍是 `/relay`
- planning target 的 shared shell 已经切到 `/control`

因此首版 repo-side 实现允许：

- 复用当前 `mail_runner/relay_server/*` 的 WebSocket server、握手、packet store 与 direct action seam
- 在 cutover 前通过 adapter / alias / compatibility layer 让 `/control` 复用 `/relay` 的内部资产

但不允许：

- 长期把 `/relay` 与 `/control` 作为两份并列的 Android-facing shared shell
- 在 `/control` 上重新发明一套绕开现有 replay / accepted store 的临时实现

## 4. `/control` 首版最小责任

### 4.1 admission 与认证

repo-side 首版必须负责：

- 暴露单一 WebSocket control ingress：`/control`
- 对 WebSocket upgrade 读取 `Authorization: Bearer <transport_token>`
- 与 `/v1/files` 共用同一 transport token verifier
- 拒绝 mailbox 凭据、query token、第二套 file token 作为 `/control` 凭据

repo-side 首版仍保留 `hello / hello_ack`，因为它们承担：

- client identity
- `supported_payload_schemas` 协商
- `accepted_payload_schemas` 回告
- `heartbeat_seconds` 协商
- `transport_token_id` 级 operator-facing 诊断

`healthz` 继续只是 debug / ops 诊断入口，不是 business truth。

### 4.2 shared message set 责任

repo-side 首版按 shared contract 接受以下 message set：

- `hello`
- `hello_ack`
- `command`
- `command_ack`
- `event`
- `result`
- `error`
- `ping`
- `pong`

repo-side 最低要求：

- `error` 只用于协议级错误，不替代业务 `result`
- `event` 只承载 push 中间事件或订阅事件
- `result` 承载最终结果或阶段性结果
- 一旦业务语义已经进入 `result`，不得再用 `error` 偷偷替换

### 4.3 `command` admission 责任

repo-side 首版对每个 `command` 至少要做以下 admission：

- 校验 `schema_version`
- 校验 `message_type = command`
- 校验 `trace.trace_id`
- 校验 `request_id`
- 校验 `packet_id`
- 校验 `command_type`
- 校验 `payload_schema`
- 对已知的 `related` 键透传，不得主动丢弃

repo-side 不负责在 `/control` 层解释全部业务字段，但必须做到：

- protocol-level 字段完整
- payload schema 可路由
- 已知相关性键可进入下游 handler、store 与 observability

### 4.4 `accepted` 与 replay 责任

repo-side 首版最关键的责任不是“尽快回 ack”，而是“只在能 replay 时回 accepted”。

冻结读法：

- `accepted = false`
  - 表示此次请求未进入 durable accepted lane
  - 可按 payload-specific 规则读成 `replayable`、`fallbackable` 或 `terminal`
- `accepted = true`
  - 只在 repo-side 已具备 replay authority 时允许返回
  - 后续必须能返回稳定的同一 `receipt_id`
  - 若已有 final result，后续必须能返回稳定的同一 `result_id`

repo-side 分责：

- relay-side durable store
  - 对 Android 暴露 accepted-path replay authority
  - 负责 `receipt_id` 连续性
  - 负责 `result_id` 对外重放连续性
- PC runtime
  - 负责 business execution truth
  - 负责 materialize canonical result / event input
  - 不直接承担 Android-facing accepted replay 身份的第一响应

accepted 后连接丢失时：

- 先 replay 同一 `request_id + packet_id`
- 不做 silent mail fallback
- 若 replay 仍失败，显式暴露 recovery failure

### 4.5 store 责任

repo-side 首版至少需要两层 durable 责任：

1. accepted request continuity
2. final result continuity

当前仓库的可复用资产：

- `PersistentAcceptedPacketStore`
  - 已经能提供 `packet_id -> receipt_id` 的连续性起点
- `mail_runner/relay_server/direct_actions.py`
  - 已有 `packet_ack` 与 `bootstrap_result` 的 repo-side direct handler seam

repo-side 首版允许：

- 先复用或扩展现有 accepted packet store
- 在其旁边补最小 result cache / result index

repo-side 首版不应：

- 把 accepted continuity 完全寄托在 PC 进程内存态
- 只缓存“收到过 packet”，却不缓存可重放的最终 result identity

### 4.6 `event` / `result` 发射责任

repo-side 首版必须明确：

- 哪些 payload 只会产出 `result`
- 哪些 payload 会先产出 `event` 再产出 `result`
- `subscription_id` 只用于持续事件流，不替代 `request_id`

repo-side 当前建议顺序：

1. `transport_probe`
   - 允许 `event`
   - 允许最终 `result`
2. `taskmail-bootstrap-control-contract-v2`
   - `command_ack` 后返回 direct `result`
3. 其他 payload
   - 在 companion contract 明确前，不抢跑进 `/control` 首刀

### 4.7 observability 责任

repo-side `/control` 不只是收发通道，还必须成为可观测通道。

最低要求：

- 每个 frame 都带 `envelope_id`
- 每个 frame 都带 `trace_id`
- harness/debug 场景透传 `probe_id`
- `command_ack`、`event`、`result` 带可用的 `related` block
- 接收、accepted、result replay 都能落到 machine-readable store 或 evidence artifact

repo-side 当前可复用的观测资产包括：

- `healthz`
- relay packet store
- bootstrap probe helper
- closeout / canonical summary / session action closeout 既有证据链

## 5. repo-side 首批实现顺序

repo-side 建议把 `/control` 首批实现顺序压成三刀：

1. `transport_probe`
2. `taskmail-bootstrap-control-contract-v2`
3. 已冻结但尚未映射进 shared shell 的 session-action direct contract

首刀不建议同时放入：

- 全量 history API
- 通用附件平台语义
- 未冻结 schema 的新 reply/control payload
- 另一套 screen-scoped direct overlay 读法

## 6. 与现有代码资产的映射

repo-side 当前最值得复用的实现资产：

- `mail_runner/relay_server/app.py`
  - WebSocket 接入、`Authorization` admission、`healthz`
- `mail_runner/relay_server/protocol.py`
  - `hello_ack`、`packet_ack`、result parsing/builder seam
- `mail_runner/relay_server/packet_store.py`
  - accepted continuity 的 durable 起点
- `mail_runner/relay_server/direct_actions.py`
  - bootstrap/new-task direct handler seam
- `mail_runner/relay_server/post_creation_actions.py`
  - post-creation direct action seam
- `mail_runner/outbound/relay_bootstrap.py`
  - repo-side bootstrap/evidence helper

vNext `/control` 的 repo-side 落地应优先“沿这些 seam 演进”，而不是平行再造一套新 server。

## 7. Merge Gate

在把 `/control` 更进一步接入 Android shared implementation 之前，repo-side 至少应具备以下证据：

1. `Authorization: Bearer <transport_token>` 在 `/control` 上已跑通
2. 至少一个 `accepted = true` 样本能 replay 出同一 `receipt_id`
3. 至少一个 final `result` 样本能 replay 出同一 `result_id`
4. `transport_probe` 至少一条场景能产出三端时间线证据
5. `/control` 新实现不让 current mail-first reply/control 发生回归

## 8. 当前结论

repo-side 对 `/control` 的首版责任已经足够明确：

- 单一认证 control ingress
- shared message set
- accepted-path replay authority
- machine-readable observability

现在真正缺的不是再讨论 `/control` 抽象，而是把 result continuity 与 payload mapping 按上述边界补成可验证实现。
