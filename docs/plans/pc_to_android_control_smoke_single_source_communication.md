# PC -> Android current-session 烟测单一交流文档（当前态 / 目标态）

## Status

- Date: 2026-03-24
- Scope: 当前 Android 可执行联调要求，以及 shared `/control` 目标态 smoke 对齐要求
- Layer: Layer 2 repository communication note
- Current truth:
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
- Related repo evidence:
  - `docs/plans/post_creation_session_action_contract_v1.md`
  - `docs/plans/post_creation_session_action_closeout_handoff.md`
  - `tests/test_relay_server_control_protocol.py`
  - `tests/test_relay_server_control_runtime.py`

本文是 **PC 侧发给 Android 侧** 的单一交流文档。

从 2026-03-24 起，本文明确分成两层：

- 当前态：Android 今天实际可执行、可收证据、可回收 closeout 的 smoke 要求
- 目标态：Android 后续如果真正迁到 shared `/control` session-action lane 时，应对齐的 smoke 要求

如果本文与 `docs/current/*` 冲突，以 `docs/current/*` 为准。

## 1. 先读结论

截至 2026-03-24，Android 当前 `reply` / `/status` 的可执行烟测，正确读法仍是：

- guarded direct packet lane
- `packet(status|reply) -> packet_ack -> canonical mail outcome`
- Android 当前还没有把 `reply` / `/status` 业务链切到 shared `/control` 的 `hello -> command_ack -> result(session_action_result)` 主路径

因此：

- 如果是在安排这周的 Android 联调、收证据、回收 closeout bundle，看第 2 到第 5 节
- 如果是在定义 Android 未来切到 shared `/control` 之后的 smoke 要求，看第 6 到第 9 节

不要再把第 6 到第 9 节的目标态 `/control` 要求，误写成 Android 今天已经必须满足的现状要求。

## 2. 这轮当前真正要解决什么

这轮当前只解决一件事：

- 验证 Android 能否在 **当前 guarded direct lane** 上，稳定完成 current-session `status` 与 plain `reply` 这条 bridge-to-mail closeout 切片

这轮烟测当前固定为两条 live 样本：

- Sample A: current-session `/status`
- Sample B: current-session plain `reply`

这轮烟测当前 **不是** 为了：

- 宣布 Android 已完成 shared `/control` 业务 cutover
- 把 current-session `status` / `reply` 误读成 direct terminal-result API
- 顺手扩 scope 到 `/pause`、`/resume`、`/end`、`/kill`、`/sessions`、history、attachments 或 structured answers
- 要求 Android 当前必须回传 `/control` `hello_ack` / `command_ack` / `result(session_action_result)` transcript

## 3. 当前 Android 可执行 smoke 要求

### 3.1 当前发送链应该怎么读

Android 当前 `reply` / `/status` 的 direct 尝试，应按下面的口径理解：

- 发送入口是当前 relay packet lane，不是 shared `/control` command lane
- 当前业务读法是：
  - `packet(status|reply) -> packet_ack`
  - accepted 后继续等待 canonical mail outcome
- `packet_ack.accepted = true` 只表示 relay 已接受这次 direct ingress 尝试
- `packet_ack.accepted = true` 不等于最终用户可见结果已经完成
- 最终用户可见结果仍要看 canonical mail 链路：
  - `/status` 看 canonical `[STATUS]` mail
  - plain `reply` 看后续正常 mail outcome

当前 packet 内承载的 session-action payload 仍应保持这几个边界：

- `schema_version = post-creation-session-action-contract-v1`
- `action` 只允许：
  - `status`
  - `reply`
- `target.scope = current_session`
- `target.workspace_id` 与 `target.session_id` 必须存在
- `target.thread_id` 已知时应带上，不应无故省略
- `status` payload 仍是空对象 `{}`
- `reply.reply_text` 仍只允许 plain 自然语言 continuation

### 3.2 当前 direct scope 边界

当前 Android direct v1 scope 仍然只覆盖：

- current-session `/status`
- current-session plain `reply`

当前仍不应写成 direct scope 的内容：

- attachments
- structured answers
- quick answer 专用语义
- paused `/resume`
- 任何非 `current_session` 的 target

当前 Android 的 session gating 也要按现状读，而不是按目标态文档脑补：

- `paused` 不应强推 direct plain `reply`
- 多问题等待态不应强推 direct plain `reply`
- 单问题 `awaiting_user_input` 当前仍可能合法走 direct plain `reply`
- 因此，不能把“处于 `awaiting_user_input` 但仍 direct plain reply”直接判成 Android 违规

### 3.3 当前轮次成功判据

对每条样本，当前成功判据至少同时满足：

- Android 发起 direct 尝试后，本地 evidence 留下稳定 send record
- direct accepted 时，可回收到：
  - `requestId`
  - 可用时的 `receiptId`
  - 可用时的 `transportMessageId`
- `/status` 最终能在 canonical mail 链路上看到对应 `[STATUS]` 结果
- plain `reply` 最终能在 canonical mail 链路上看到对应 continuation outcome
- PC 侧能够把 Android send record 与 `session_action_closeout.json` / closeout bundle 对上

当前这轮 **不再把下面这些项写成 Android 必交要求**：

- 同一 `packet_id` 的 accepted-path replay continuity
- 同一 `packet_id` 的 `result_id` continuity
- `/control` `hello_ack`
- `/control` `command_ack`
- `/control` `result(session_action_result)` transcript

这些都属于第 6 节开始的目标态 `/control` smoke 要求，不是 Android 当前 closeout 的硬前提。

## 4. 当前希望 Android 回传的交付物

这轮当前希望 Android 至少回传：

- 一份包含本轮样本的 Android send records 导出
  - 当前优先使用 `taskmail_session_action_send_records.json`
- 每条样本对应的最小定位信息：
  - 样本类型：`/status` 或 plain `reply`
  - 发送时间
  - `workspace_id`
  - `session_id`
  - 已知时附上 `thread_id`
- 每条样本对应的最小 canonical mail outcome 证据：
  - `Message-ID`
  - 或 subject / 时间戳 / 客户端截图三者之一的稳定组合
- 如果样本失败：
  - 失败时刻的 send record
  - Android 可见错误文案或截图
  - 不要只给口头结论

PC / operator 侧当前对账主工具仍是：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root .\tasks --android-send-records <android_send_records.json> --output <bundle.json>
```

## 5. 当前轮次建议执行顺序

1. 先确认 PC 侧 runner 与 `Relay task-root sync` companion 正常，不要在 companion 缺失时先怀疑 Android direct lane
2. 先跑一条 current-session `/status`
3. 导出或分享本轮 `taskmail_session_action_send_records.json`
4. 收集 canonical `[STATUS]` mail 证据
5. 再跑一条 current-session plain `reply`
6. 再次导出或分享本轮 `taskmail_session_action_send_records.json`
7. 收集 canonical mail outcome 证据
8. PC 侧据此构建 closeout bundle，对账 `requestId` / `receiptId` / `transportMessageId` / `ingress_message_id`

当前顺序仍建议固定成先 `status`、后 `reply`。

## 6. 目标态 `/control` smoke（尚未成为当前 Android 要求）

只有当 Android 后续真的把 `reply` / `/status` 迁到 shared `/control` session-action lane，下面这些要求才升级为当前要求。

在那之前，本节应读成 **目标态 contract 对齐说明**，不是 Android 今天的 hard blocker。

### 6.1 Admission / hello

- 连接入口是 shared `/control` websocket
- `/control` 与 `/relay` 当前复用同一 relay host / port
- `Authorization: Bearer <transport_token>` 放在 WebSocket header，不放在 JSON body
- 进入业务前必须先做 `hello -> hello_ack`
- 必须读取 `hello_ack.accepted_payload_schemas`
- 只有当 `accepted_payload_schemas` 包含 `post-creation-session-action-contract-v1` 时，才允许发送这轮烟测的 `status` / `reply`
- `hello_ack.transport_token_id` 只是 token fingerprint 级别的 operator 诊断，不是 token 本体，也不是业务 identity

### 6.2 Command 边界

- `message_type` 固定为 `command`
- `payload_schema` 固定为 `post-creation-session-action-contract-v1`
- `command_type` 当前只允许：
  - `status`
  - `reply`
- `target.scope` 固定为 `current_session`
- `target` 当前必须带：
  - `workspace_id`
  - `session_id`
- `target.thread_id` 当前不是必填，但 **强烈建议带上**；已知 thread identity 时不应省略
- `request_id` 必须稳定对应一次 visible send attempt
- `packet_id` 必须稳定对应这次 direct-send 的 replay identity
- 推荐保留 `trace.trace_id` 与 `related.ui_surface`，方便 PC / Android 两侧对账
- `status` payload 仍应是空对象 `{}`
- `reply.reply_text` 仍只允许 plain 自然语言 continuation

### 6.3 结果读法

目标态 `/control` 上这条切片的业务流固定读作：

- `command(status|reply) -> command_ack -> result(session_action_result)`

Android 迁到这条 lane 之后，应按下面的口径消费：

- `command_ack.accepted = true` 只表示 relay 已把请求接入 durable accepted lane
- `command_ack.accepted = true` 不等于 final business success
- 真正的 direct result 是后续的 `result(session_action_result)`
- `session_action_result` 也 **不是** terminal business result
- `session_action_result` 当前只表示：
  - `result_scope = mail_ingress_submission`
  - canonical outcome 仍经由 `mail`
  - 伴随返回一份 `session_action_closeout` anchor snapshot

目标态应重点读取这些字段：

- `result.result_type = session_action_result`
- `result.status`
- `result.payload.session_action_result.result_scope`
- `result.payload.session_action_result.canonical_outcome_via`
- `result.payload.session_action_result.transport_message_id`
- `result.payload.session_action_result.session_action_closeout`

### 6.4 目标态成功判据

`/control status` 与 `/control reply` 的目标态成功判据，至少应同时满足：

- `hello_ack.accepted_payload_schemas` 包含 `post-creation-session-action-contract-v1`
- `command_ack.accepted = true`
- 收到 `result(session_action_result)`
- `result.status = completed`
- `result_scope = mail_ingress_submission`
- replay 同一 `packet_id` 时，返回同一 `receipt_id`
- 若 final result 已物化，replay 同一 `packet_id` 时返回同一 `result_id`
- 最终用户可见结果仍回 canonical mail，而不是停在 ack/result

## 7. Android 侧注意事项

### 7.1 不要误读当前 scope

- 当前态仍是 bridge-to-mail
- direct accepted 不等于业务完成
- 当前态要关的是 closeout 与 canonical mail continuity，不是 `/control` transcript 完整性

### 7.2 locator 问题先看 PC 侧 companion

如果 current-session `status` / `reply` 报 locator resolution 失败，不要第一时间回头怀疑 Android direct lane。

PC 侧当前已知先看：

- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup`
- `.\_tmp_live_mail_runner\host_state.json`
- `.\_tmp_live_mail_runner\loop.stderr.log`
- `Relay task-root sync` companion 是否在跑

建议把这一步读成 **当前 smoke 的前置条件检查**，至少确认两件事：

- host 不是 stale，且脚本最终没有落到 `Mail runner is not running.`
- `Relay task-root sync` companion 处于 `enabled=True` 且 `running=True`

如果 relay-enabled host 在跑，但 task-root sync companion 缺失，current-session `reply` / `/status` 仍可能因为 VPS `task_root` 快照落后而失败。

不要只跑一个未带参数的 bare `status` 就下结论；对这轮 smoke，优先固定到 `mail_config.bot.relay.local.yaml + _tmp_live_mail_runner` 这一组 relay-enabled runtime 再判断。

### 7.3 不要扩 scope

- 当前轮次不要顺手上 `/pause`
- 当前轮次不要顺手上 `/resume`
- 当前轮次不要顺手上 `/end`
- 当前轮次不要顺手上 `/kill`
- 当前轮次不要顺手上 `/sessions`
- 当前轮次不要顺手上 attachments / structured answers / quick answer
- 当前轮次不要把 `/control` 当作 generic history / session / control API

## 8. PC 侧已提供给 Android 的工具 / 材料

### 8.1 当前 closeout 可直接复用的材料

- `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json`
- `tasks/<thread_id>/runs/<task_id>/canonical_summary.json`
- closeout bundle 脚本：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root .\tasks --android-send-records <android_send_records.json> --output <bundle.json>
```

- hosted runner / companion 状态检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

### 8.2 目标态 `/control` 可直接参考的 repo 材料

- current truth:
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/mail_protocol.md`
- command/result 示例与断言：
  - `tests/test_relay_server_control_protocol.py`
  - `tests/test_relay_server_control_runtime.py`
- contract freeze / closeout handoff：
  - `docs/plans/post_creation_session_action_contract_v1.md`
  - `docs/plans/post_creation_session_action_closeout_handoff.md`

## 9. 当前结论

这轮 Android 烟测的默认口径现在应固定成：

- 当前只测 current-session `/status`
- 当前只测 current-session plain `reply`
- 当前先关 Android send record 与 canonical mail closeout
- 目标态 `/control` `command_ack -> result(session_action_result)` continuity 仍保留，但暂不当作 Android 今日 hard requirement

只要沿这条窄口径收证据，就足够支持下一轮是否继续扩大 `/control` session-action scope 的判断；在此之前，不应把目标态 `/control` smoke 要求误读成 Android 已经必须满足的当前要求。
