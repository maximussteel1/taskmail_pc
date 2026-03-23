# Android `transport_probe` 联调要求与当前读法

## Status

- Date: 2026-03-24
- Scope: 发给 Android / operator 侧的 shared `/control transport_probe` 首轮联调要求、当前真机读法、已验证现状、交付证据与可用工具
- Layer: Layer 2 repository handoff / requirements
- Current truth:
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
- Wire / evidence entry:
  - `tests/test_relay_server_control_runtime.py`
  - `tests/test_relay_server_transport_probe.py`
  - `_tmp_relay_smoke_20260324/control_transport_probe_projected_observed_live_smoke.json`
  - `_tmp_relay_smoke_20260324/control_transport_probe_live_smoke.json`

本文件不改写 `docs/current/*`。
如果本文件与 `docs/current/*` 冲突，以 `docs/current/*` 为准。

## 1. 这次联调要解决什么

这次联调不是为了继续扩 `/control` 的 payload family，也不是为了把 `transport_probe` 升格成终端用户能力。

这次联调只解决一件事：

- 把 Android / operator 对当前 `transport_probe_result` 的消费口径固定下来

具体说，这轮最初要求 Android 侧证明：

- 能完成 `/control` 的 `hello -> hello_ack`
- 能在 `accepted_payload_schemas` 包含 `taskmail-transport-probe-payload-v1` 时发起 `command(transport_probe)`
- 能消费 `command_ack -> event* -> result(transport_probe_result)`
- 能在 `accepted=true` 后按同一组 `packet_id` / `request_id` 做 replay
- 能把 `completed / partial / failed` 与 `observed / timed_out / submitted / failed` 的组合读成稳定 UI / operator 语义

截至 2026-03-24，这轮首批 Android/operator 联调要求已获得真机正向样本，不再是“等待 Android 侧接入”的纯前置条件。

## 2. 当前 repo-side 已经确认的事实

截至 2026-03-24，repo-side 已经把以下事实落地并验证：

- relay 在 transport probe mail 提交成功后，会按 `timeout_seconds` 轮询 relay-visible `task_root` 下的 `tasks/_mailbox/transport_probes/<probe_id>.json`
- relay 读取 sidecar 时，会校验 `request_id` / `packet_id` / `trace_id` / `transport_message_id` 与当前 probe 对齐
- PC host 收到 deterministic probe mail 后，会把 mailbox observation sidecar 写到本地 authoritative task root 的 `tasks/_mailbox/transport_probes/<probe_id>.json`
- relay-side `transport_probe_result.payload.observation` 现在复用这份 PC sidecar evidence，而不是另造第二份 proof surface

repo-side 当前 automated verification：

- `2026-03-24` 运行 `.venv\Scripts\python.exe -m pytest`
- 结果：`443 passed`
- 相关新增覆盖：
  - `tests/test_relay_server_transport_probe.py`
  - `tests/test_relay_server_control_runtime.py`
  - `tests/test_app_phase2.py`

repo-side 当前 live evidence：

- VPS relay 已重部署到 `release_20260324_011219`
- `_tmp_relay_smoke_20260324/control_transport_probe_projected_observed_live_smoke.json` 记录了 `2026-03-24 01:13:39` 的 `transport_probe_result`
- 该次结果是：
  - `status=completed`
  - `payload.outcome=observed`
  - replay 复用了同一 `receipt_id` / `result_id` / `event_id`
- 本机 PC host 这轮未重启
- 对应 PC observation sidecar 在：
  - `_tmp_live_mail_runner/tasks/_mailbox/transport_probes/probe_live_result_observed_20260324_011330.json`
- PC 实际收件记录在：
  - `_tmp_live_mail_runner/loop.stderr.log`
- sync companion 把这次变更推到 relay-visible task root 的记录在：
  - `_tmp_live_mail_runner/relay_task_root_sync.stdout.log`

结论：repo-side 当前不缺“服务端是否已经真的能把 observation 投影进 result”的证据。
当前缺的是 Android 侧把这组结果消费成稳定 operator 语义。

## 2.1 Android 仓最新真机读法

Android 仓当前已把以下内容记为真机正向事实：

- `/control transport_probe` 已拿到同一 `probe_id` 的 `command_ack -> event* -> result`
- accepted 后对同一 `packet_id` / `request_id` replay，会稳定复用同一组 `receipt_id` / `event_id` / `result_id`
- 当前代表性真机样本 `probe_id=probe_6d89e1f6e1414c38be191aa8eed582af`

这些事实与本仓 current contract 是对齐的：

- `transport_probe` 仍是 operator/debug harness
- replay continuity 仍以 `packet_id` / `request_id` 为主锚点
- 这轮 evidence 不应被误读成 `/control` 业务 cutover 已完成

## 3. 本轮联调范围

Android 侧本轮只需要覆盖以下范围：

- shared `/control` websocket
- `transport_probe` command
- `command_ack -> event* -> result` 消费
- accepted 后 reconnect / replay continuity
- `transport_probe_result.payload.observation` 的展示或日志落盘

本轮明确不做：

- 不把 `transport_probe` 当成终端用户动作
- 不把 `/control` 当成 generic Android 主协议
- 不在这一轮里顺手迁移新的 `/control` 业务 action
- 不要求 Android 本轮处理 `new_task`、current-session direct `/status` / `reply`、Phase 3 detail subscribe

## 4. Android 侧必须遵守的读法

### 4.1 Admission / bootstrap

- `/control` 与 `/relay` 当前复用同一 relay host / port / `Authorization: Bearer <transport_token>` 认证路径
- Android 必须先发 `hello`，再等待 `hello_ack`
- Android 必须读取 `hello_ack.accepted_payload_schemas`
- 只有当 `accepted_payload_schemas` 包含 `taskmail-transport-probe-payload-v1` 时，才允许发送 `transport_probe`

### 4.2 当前只支持的 `transport_probe` payload 边界

- `payload_schema` 必须是 `taskmail-transport-probe-payload-v1`
- `command_type` 必须是 `transport_probe`
- `scenario` 当前只支持 `android_direct_ping_to_vps_to_pc`
- `direction` 当前只支持 `android_to_pc`
- `transport_kind` 当前只支持 `mail`
- `trace.probe_id` 必须等于 `payload.probe_id`
- `payload.payload_text` 必须是单行文本
- `payload.timeout_seconds` 必须是正整数

Android 不要把当前 harness slice 误读成“任意 probe 变体都可发”。

### 4.3 对 `command_ack` 的正确理解

- `command_ack.accepted=true` 表示 relay 已把请求接受进 replayable store
- `command_ack.accepted=true` 不等于 final success
- Android 不要把 `command_ack` 当作业务完成信号
- `command_ack.transport_message_id` 在常见成功路径里会出现
- 但在“accepted 后 mail submission 失败”的路径里，`transport_message_id` 可以为空；最终仍要看 `result`

### 4.4 对 `event*` 的正确理解

- 当前业务流是 `command_ack -> event* -> result`
- `event*` 是诊断时间线，不是 Android 业务真相层
- Android 可以展示或记录 event timeline
- Android 不应把“必须收到固定数量的 event”写死成成功条件
- Android 侧真正的终态判断应以 final `result` 为准

### 4.5 对 `transport_probe_result` 的固定消费口径

- `status=completed` 且 `payload.outcome=observed`
  - 读法：成功
  - 含义：relay 已提交 deterministic probe mail，并且已经读到与当前 probe identity 对齐的 PC mailbox observation
- `status=partial` 且 `payload.outcome=timed_out`
  - 读法：诊断态，不是业务成功
  - 含义：mail 已提交，但在 `timeout_seconds` 内没有读到匹配的 PC observation
- `status=partial` 且 `payload.outcome=submitted`
  - 读法：诊断态，不是业务成功
  - 含义：mail 已提交，但 relay 当前无法进行 PC observation lookup，典型原因是 relay-visible `task_root` 不可用
- `status=failed` 且 `payload.outcome=failed`
  - 读法：失败
  - 含义：relay 在 mail submission 完成前失败

补充要求：

- Android 对 `partial` 不要显示成成功
- Android 对 `payload.observation` 应作为 operator diagnostics 展示或落日志
- Android 不要把 `payload.observation` 单独当成新的业务真相层

### 4.6 Replay continuity

- 一旦 `command_ack.accepted=true`，后续如果连接断开，Android 必须优先 reconnect 并 replay 同一组 `packet_id` / `request_id`
- 对同一 `packet_id` 的 replay，当前 repo-side 已冻结的读法是：
  - 返回同一 `receipt_id`
  - 如果 final result 已物化，则返回同一 `result_id`
  - 对当前 `observed` 路径，event replay 也会复用同一组 `event_id`
- 只有新的 operator 点击，才应生成新的 `packet_id` / `request_id` / `probe_id`

## 5. Android 侧联调最小交付物

Android 侧本轮最小交付物最初建议固定为：

- 一个 operator/debug 入口，能向 `/control` 发起 `transport_probe`
- 一份完整的原始 JSON transcript
- transcript 至少包含：
  - `hello_ack`
  - 第一轮 `command_ack`
  - 第一轮全部 `event`
  - 第一轮 final `result`
  - replay `command_ack`
  - replay final `result`
- 一份 Android 侧消费结论，明确写出：
  - `completed + observed` 如何展示
  - `partial + timed_out` 如何展示
  - `partial + submitted` 如何展示
  - `failed + failed` 如何展示
- 如果首轮 live 只跑到 `observed`，Android 仍应至少用 PC-side transcript / fixture 证明 `partial` 与 `failed` 的 UI 或日志分支已经接好

截至 2026-03-24，以上交付物里的核心项已经有首轮真机正向 evidence；这份文档后续主要承担“不要把当前 evidence 误升级成更宽 cutover 结论”的边界说明。

## 6. Android 侧可以直接复用的材料与工具

### 6.1 当前仓库里的直接材料

- current truth：
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
- wire-level test fixtures：
  - `tests/test_relay_server_control_runtime.py`
  - `tests/test_relay_server_transport_probe.py`
- live transcripts：
  - `_tmp_relay_smoke_20260324/control_transport_probe_projected_observed_live_smoke.json`
  - `_tmp_relay_smoke_20260324/control_transport_probe_live_smoke.json`
  - `_tmp_relay_smoke_20260324/control_transport_probe_pc_observed_live_smoke.json`

这些材料已经足够让 Android 侧在不猜协议的前提下接入当前联调。

### 6.2 Android 侧最适合复用的现有能力

- 现有 `/relay` WebSocket transport 层
  - 因为 `/control` 与 `/relay` 当前复用同一 relay host / port / token admission 路径
- 现有 raw JSON inbound / outbound 日志能力
- 现有 reconnect / replay 处理骨架

如果 Android 侧已经有 `/relay` 的连接、header 注入、frame logger，本轮不建议另写一套新的 transport stack。

### 6.3 手工调试工具要求

如果 Android 侧想先做手工探测，工具只需要满足以下能力：

- 能连 WebSocket
- 能设置 `Authorization: Bearer <transport_token>`
- 能手工发送 JSON frame
- 能导出原始收发 transcript

仓库当前没有专门提供一个 Android-side probe harness app；repo-side 当前提供的是 current docs、tests 和 live transcript evidence。

### 6.4 PC 侧可配合提供的辅助证据

如果 Android 侧需要 PC/operator 配合排障，当前可看的第一手证据是：

- `tasks/_mailbox/transport_probes/<probe_id>.json`
- `_tmp_live_mail_runner/loop.stderr.log`
- `_tmp_live_mail_runner/relay_task_root_sync.stdout.log`

这三处分别回答：

- PC 是否真的收到了 probe mail
- PC 是否真的写出了 mailbox observation sidecar
- sidecar 是否真的被同步到了 relay-visible `task_root`

## 7. 建议的首轮联调步骤

1. Android 侧先拿到当前 live relay endpoint 与 transport token。
2. Android 侧连接 `/control`，发送 `hello`，确认 `hello_ack.accepted_payload_schemas` 包含 `taskmail-transport-probe-payload-v1`。
3. Android 侧发送一条 `transport_probe` command，并完整记录 `command_ack -> event* -> result`。
4. Android 侧确认 final `result` 的 `status` / `payload.outcome` / `payload.observation` 被正确消费。
5. Android 侧主动断开后重连，使用完全相同的 `packet_id` / `request_id` / `probe_id` replay。
6. Android 侧确认 replay continuity：
  - `receipt_id` 不变
  - 若 final result 已物化，则 `result_id` 不变
  - 当前 `observed` 路径下，event replay 也应稳定
7. Android 侧输出 transcript 和消费结论，回传给 PC / relay 侧复核。

## 8. 可直接参考的最小报文

`hello` body 示例：

```json
{
  "message_type": "hello",
  "client_id": "android-control",
  "client_version": "0.1.0",
  "transport_token_id": "<token fingerprint>",
  "supported_payload_schemas": [
    "taskmail-bootstrap-control-contract-v2",
    "taskmail-transport-probe-payload-v1"
  ],
  "sent_at": "2026-03-24T10:00:00"
}
```

`command(transport_probe)` body 示例：

```json
{
  "message_type": "command",
  "request_id": "probe_req_001",
  "packet_id": "android-control:transport-probe:probe_req_001",
  "command_type": "transport_probe",
  "payload_schema": "taskmail-transport-probe-payload-v1",
  "trace": {
    "trace_id": "trace-transport-001",
    "probe_id": "probe-transport-001"
  },
  "payload": {
    "probe_id": "probe-transport-001",
    "scenario": "android_direct_ping_to_vps_to_pc",
    "direction": "android_to_pc",
    "transport_kind": "mail",
    "payload_text": "PING transport probe",
    "timeout_seconds": 180
  },
  "related": {
    "ui_surface": "transport_probe_sheet"
  },
  "sent_at": "2026-03-24T10:00:00"
}
```

注意：

- `Authorization: Bearer <transport_token>` 在 WebSocket header，不在 JSON body
- 当前 `transport_token_id` 只是 token fingerprint，不是 token 本体
- live smoke 使用过的 `/control` URL 和 transcript 已在 `_tmp_relay_smoke_20260324/` 留档，但 live token 仍应通过 operator 路径单独下发

## 9. 当前结论

repo-side 对这一轮的判断应固定为：

- `transport_probe` 的 relay-side projected observation 已落地并过 live
- Android / operator 首轮联调已经拿到真机正向样本
- 下一步不再是“证明 transport_probe 能不能接上”，而是继续保持当前 harness/debug slice 的窄边界，不要把它误升级成新的业务 cutover
- 与 transport readiness / observability 相邻的 `/v1/files` 当前也已有 debug-only 单样本真机闭环，但仍只闭到文本文件单样本，不代表更宽矩阵已完成

只要 Android 侧把上述消费口径、replay continuity 和 transcript 产出补齐，这一小段链路就可以视为双边闭环。
