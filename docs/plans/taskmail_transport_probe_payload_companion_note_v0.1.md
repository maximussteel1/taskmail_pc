# TaskMail `transport_probe` Payload Companion Note（v0.1）

更新时间：2026-03-23

## 状态

- 本文是 `mail_based_task_manager` 仓对 `taskmail-transport-probe-payload-v1` 的 repository-side companion note。
- 本文不改写 `docs/current/*` 的 current behavior truth。
- 本文的作用是：把 repo-side 对 probe payload、时间线、mail 映射、文件面基线与实现职责的承认写清楚，避免 probe 脚本再长成脚本私有协议。

## Read First

- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-transport-probe-payload-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-transport-observability-harness-v0.1.md`
- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `docs/plans/taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `docs/plans/taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`
- `docs/current/android_runner_communication_contract.md`
- `docs/platform/relay_transport_protocol_draft.md`

## 1. 仓库侧承认什么

repo-side 当前承认以下 probe baseline 可以作为后续实现前提：

- `payload_schema = taskmail-transport-probe-payload-v1`
- probe 是 shared transport shell 上的正式 payload，不是脚本私有 envelope
- relay carrier 使用 `command / event / result`
- mail carrier 使用确定性的 subject + `text/plain` header block
- probe timeline 必须带 `clock_source`、`monotonic_ms`，可选 `clock_offset_ms`

这表示：

- probe 脚本、relay-side packet history、PC-side probe watcher 应读同一份 payload 语义
- 不应再把 probe 写成“脚本自己拼一个 `PING` 字符串，解析全靠猜”

## 2. 当前不改什么

本 note 不改变以下 current-truth 读法：

- 当前 Android-facing current behavior 仍由 `docs/current/android_runner_communication_contract.md` 描述
- 当前 mail ingress / artifact_index / reporter 仍然按 `docs/current/*` 行为工作
- 本 note 不代表 repo-side 已经实现 `/control`、`/v1/files` 或新 probe watcher

## 3. repo-side 同意的 probe command 语义

repo-side 同意 Android 文档中的 probe command 字段：

- `probe_id`
- `scenario`
- `direction`
- `transport_kind`
- `payload_text`
- `timeout_seconds`

repo-side 当前接受的 scenario baseline：

- `android_mail_ping_to_pc`
- `android_direct_ping_to_vps_to_pc`
- `pc_mail_ping_to_android`
- `pc_mail_ping_reply_loop`

repo-side 约束：

- `payload_text` 只承担 transport 观察载荷，不承担业务命令语义
- `trace.probe_id` 与 payload 内 `probe_id` 必须一致
- 一旦 relay path 分配了 `request_id` / `packet_id`，后续 probe event / result 必须尽可能回填相同 identity

## 4. repo-side 同意的 probe event / result 语义

repo-side 同意以下 event timeline 读法：

- outer `event_type` 使用 probe 事件枚举
- inner `payload.probe_event_type` 与 outer `event_type` 一致
- `timeline.clock_source`、`timeline.monotonic_ms` 是 probe 报表的强制字段

repo-side 当前承认的首批 probe 事件包括：

- `vps_probe_packet_received`
- `vps_probe_packet_accepted`
- `vps_probe_bridge_started`
- `vps_probe_bridge_finished`
- `vps_probe_result_started`
- `vps_probe_result_finished`
- `vps_probe_rejected`
- `pc_probe_mail_observed`
- `pc_probe_handler_started`
- `pc_probe_handler_finished`
- `pc_probe_mail_submitted`

repo-side 当前承认的 final result 读法：

- outer `result_type = transport_probe_result`
- outer `status` 继续复用 shared shell：`partial` / `completed` / `failed`
- inner `payload.outcome` 继续复用 Android companion contract：`observed` / `replied` / `timed_out` / `rejected` / `failed`

## 5. repo-side 同意的 mail 映射

repo-side 同意以下 mail subject baseline：

- `android_mail_ping_to_pc`
  - `[TPROBE][A2P][MAIL] <probe_id>`
- `pc_mail_ping_to_android`
  - `[TPROBE][P2A][MAIL] <probe_id>`
- `pc_mail_ping_reply_loop` ack
  - `[TPROBE][A2P][MAIL][ACK] <probe_id>`

repo-side 同意 body 使用固定 header block：

```text
Probe-Version: taskmail-transport-probe-payload-v1
Probe-Id: probe_01JQ...
Scenario: android_mail_ping_to_pc
Direction: android_to_pc
Transport-Kind: mail
Timeout-Seconds: 180
Payload-Text: PING mailbox path
```

repo-side 读法：

- 这个 body block 是 deterministic parser target，不是给人自由发挥的自然语言 mail
- quoted reply、不相关业务字段、附带大段正文都不属于第一版 probe baseline

## 6. repo-side 文件面基线

probe v1 默认是 text-only，但 repo-side 同意保留一个极小 file-plane 验证位：

- 可通过 shared envelope `artifacts` 携带最多一个 `role = debug_artifact` 的文件引用
- `single_file_upload_limit_bytes = 33554432`（32 MiB）
- `inline_preview_max_bytes = 65536`

repo-side 选择 `32 MiB` 的原因是：

- 它与当前 relay runtime / tests 已使用的 `32 * 1024 * 1024` ceiling 保持同量级
- 第一版 `/control` 与 `/v1/files` 不应比 repo-side 既有 relay ceiling 更早出现隐式截断

## 7. repo-side 实现责任

repo-side 后续实现 probe 时，责任边界建议冻结为：

### 7.1 relay-side

- 负责 accepted-path `command_ack` 与 durable `receipt_id` / `result_id`
- 负责 probe packet history 与 probe event 持久化
- 负责 `/v1/files` file object 与 metadata truth

### 7.2 PC runtime

- 负责观察 mail probe 或消费 direct probe
- 负责写出 `pc_probe_*` 事件与本地 probe artifact
- 负责在需要文件面样本时，把 repo-local bytes 上传到 `/v1/files`

### 7.3 probe scripts

- 只做 orchestration、query、watch、report
- 不应私自定义另一套 `probe_id`、另一套 body 语法、另一套结果枚举

## 8. 下一步建议

repo-side 下一步最合理的是：

1. 把 probe `command -> command_ack -> result` 真的接到 `/control` 首版实现骨架。
2. 把 probe debug artifact 上传下载接到 `/v1/files` 首版实现骨架。
3. 让 probe debug artifact 复用统一 sidecar 与 token/reconnect/upload-error 基线。
4. 把旧 live smoke 脚本上的 probe 相关逻辑逐步吸收到统一 probe 脚本族。

## 9. 当前结论

repo-side 现在已经可以明确承认：

- `taskmail-transport-probe-payload-v1` 是 shared contract 的正式 payload
- probe mail mapping、timeline 字段、result outcome 与文件面上限已经有 repo-side baseline
- 后续脚本与实现不应再发散出第二套 probe 语义

但当前仍不应误读成：

- repo-side `/control` 与 `/v1/files` 已经实现
- probe watcher / report / relay durable store 已经全部落地
