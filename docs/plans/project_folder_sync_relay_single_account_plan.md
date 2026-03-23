# Project Folder Sync Relay Single-Account MVP Plan

> Superseded on 2026-03-23 by `docs/plans/taskmail_bootstrap_control_contract_v2.md`.
>
> 本文件保留的是较早的 bridge-to-mail single-account alternative。
> 当前跨仓默认对齐目标已经切到 `[SYNC]` 的 `direct request + direct result` roundtrip contract，
> 不再把“accepted 后桥接回 canonical `[SYNC]` mail ingress”读作 active direction。

## 状态

- 日期：2026-03-23
- 范围：仓库侧 `[SYNC]` relay direct 单账号 MVP issue / plan
- 层级：Layer 2 仓库实现计划
- 前提：当前 mail-first `[SYNC]` 已在代码和 current docs 中落地；本文只描述新的 repo-side direct 请求入口，不改写 `docs/current/*`

## Read First

- `docs/current/mail_protocol.md`
- `docs/current/android_runner_communication_contract.md`
- `docs/plans/project_folder_sync_entry_plan.md`
- `docs/plans/vps_relay_deploy_runbook.md`
- `mail_runner/app.py`
- `mail_runner/project_folder_sync.py`
- `mail_runner/relay_server/app.py`
- `mail_runner/relay_server/direct_actions.py`
- `tests/test_app_phase2.py`
- `tests/test_relay_server_app.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- Android-side companion note:
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-next-session-handoff-2026-03-23-sync-relay-single-account-plan.md`

## 问题定义

当前 `[SYNC]` 已经是 canonical mail control-plane 动作：

- `Subject: [SYNC]`
- 不创建 task
- 不创建 thread/session
- 返回 `[SYNC] Project Folder List`
- Android 继续从本地邮箱读取最新结果

但当前 relay direct ingress 仍然只覆盖：

- `phase2-direct-outbound-contract-v1` `new_task`
- post-creation current-session `/status`
- post-creation current-session plain `reply`

这意味着 Android formal-host 即使已经有 relay bootstrap 和 direct send 基线，`Project list -> Sync project list` 仍然只能走 mail，而不能复用当前 `/relay` direct lane。

Android 侧希望新增的不是第二套 `[SYNC]` 结果协议，而是：

1. `[SYNC]` 请求入口可以走 relay direct
2. relay 接受后桥接回 canonical `[SYNC]` mail ingress
3. PC 侧继续生成 canonical `[SYNC] Project Folder List`
4. Android 侧继续按现有 mail reader 读结果

## 目标

本 issue 的单账号 MVP 目标固定为：

1. relay 接受一个新的 direct `[SYNC]` packet
2. relay 将该 packet 桥接成 canonical `[SYNC]` mail 发到 bot mailbox
3. PC 侧继续复用当前 `_handle_project_folder_sync(...)`
4. 最终用户可见结果仍然只有 `[SYNC] Project Folder List`
5. `[SYNC]` 仍然保持 bootstrap 边界，不进入 task/session projection

## 非目标

本轮明确不做：

- 不新增 relay-native `[SYNC]` 结果协议
- 不把 `[SYNC]` 做成 `session_update`、detail push、workspace card 或 session card
- 不让 relay 直接列目录并绕过当前 mail control plane
- 不按 Android 本地 `sender_account_uuid` 在服务端做账号映射
- 不宣称多账号 sender mailbox 都已正确支持 direct `[SYNC]`

## 推荐方案

### 方案选择

推荐采用：

- `direct request -> relay mail bridge -> canonical [SYNC] mail ingress -> canonical [SYNC] Project Folder List reply`

不推荐采用：

- `direct request -> relay/server native project list response`

原因：

1. 当前 `[SYNC]` 的结果 truth 已经是 canonical mail reply，而不是 relay packet result
2. Android 当前已经有稳定的 `[SYNC]` result reader / parser / UI
3. 复用现有 `_handle_project_folder_sync(...)` 可以最小化语义漂移
4. `[SYNC]` 本来就不属于 task/session 状态流，没必要为它发明第二套 read surface

### 建议 packet 形状

MVP 可新增一个独立 bootstrap schema，例如：

```json
{
  "schema_version": "taskmail-bootstrap-control-contract-v1",
  "action": "sync_project_folders",
  "request_id": "req_xxx",
  "origin": {
    "client": "android_taskmail",
    "sender_account_uuid": "acc_xxx"
  },
  "sync_project_folders": {}
}
```

对应 `dispatch_metadata`：

```json
{
  "channel": "taskmail_android_direct",
  "schema_version": "taskmail-bootstrap-control-contract-v1",
  "action": "sync_project_folders",
  "fallback_policy": "mail"
}
```

这里的 `origin.sender_account_uuid` 仅作客户端附带上下文；MVP 不将其作为服务端账号映射键。

建议同时冻结 outer relay wrapper 的最小约束：

```json
{
  "message_type": "packet",
  "packet_id": "android-taskmail:sync-project-folders:req_xxx",
  "client_trace_id": "req_xxx",
  "task_run_packet": {
    "schema_version": "taskmail-bootstrap-control-contract-v1",
    "action": "sync_project_folders",
    "request_id": "req_xxx",
    "origin": {
      "client": "android_taskmail",
      "sender_account_uuid": "acc_xxx"
    },
    "sync_project_folders": {}
  },
  "dispatch_metadata": {
    "channel": "taskmail_android_direct",
    "schema_version": "taskmail-bootstrap-control-contract-v1",
    "action": "sync_project_folders",
    "fallback_policy": "mail"
  },
  "sent_at": "2026-03-23T12:30:00"
}
```

其中：

- `client_trace_id` 必须等于 inner `request_id`
- relay-level retry 在用户尚未看到 failure 前，必须复用同一组 `packet_id` 与 `request_id`
- 只有 fresh user tap 才允许换新 `packet_id` / `request_id`
- 对同一 `packet_id` 的重复 relay handling，`receipt_id` 必须稳定

## 仓库侧实现边界

### VPS / relay

建议新增一个新的 direct handler，例如：

- `RelayTaskMailDirectProjectSyncMailBridge`

职责固定为：

1. 校验 direct `[SYNC]` packet
2. 向 `taskmail_bot_mailbox_addr` 发送一封 canonical mail：
   - `Subject: [SYNC]`
   - body 为空字符串即可
   - headers 带：
      - `X-TaskMail-Direct: 1`
      - `X-TaskMail-Relay-Packet-Id`
      - `X-TaskMail-Relay-Request-Id`
3. accepted-path 返回 relay `packet_ack`
   - `accepted=true`
   - `transport_message_id` 为 bot mailbox ingress mail 的最终 `Message-ID`

第一轮不建议让这个 handler 直接调用目录扫描 helper 生成结果邮件；它应该像 `RelayTaskMailDirectNewTaskMailBridge` 一样，只负责桥接进现有 mail ingress。

### wire-level 返回约束

为了避免 Android / relay 双方各自猜测，本方案补充冻结：

1. pre-accept reject：
   - payload 不合法、schema 不匹配、`client_trace_id != request_id`、`origin.client` 不匹配等，返回 relay `error`
   - 这些属于 hard rejection
2. post-accept temporary bridge failure：
   - 例如 bot mailbox bridge 暂时不可用、SMTP 发送失败，返回 relay `error`
   - 这些属于 fallback-classified rejection
3. accepted-path：
   - 仅在 canonical `[SYNC]` ingress mail 已成功投递到 bot mailbox 时返回 `packet_ack accepted=true`
   - `packet_ack.accepted=true` 只表示 direct request 已进入 canonical mail lane，不等于 Android 已拿到最终 `[SYNC] Project Folder List`
4. 本轮不要求为 `[SYNC]` 引入新的 relay-native result frame，也不要求 Android 从 ack 直接推断最终邮箱结果身份

### PC runtime

PC runtime 应继续以当前 `[SYNC]` mail 语义为 authority：

- `mail_runner.app::_handle_project_folder_sync(...)`
- `mail_runner.project_folder_sync::build_project_folder_sync_body(...)`

repo-side 本轮需要保证的是：

1. relay bridge 注入的 `[SYNC]` mail 与当前用户直接发送的 `[SYNC]` mail 行为一致
2. `[SYNC] Project Folder List` 仍然全局只保留最新一封
3. 不创建 task/thread/session
4. 不写入 task-specific `state capsule` 或 `question capsule`

如无必要，不建议为此重写 `app.py` 的 `[SYNC]` 主逻辑；优先只加 bridge 和测试。

## 单账号 MVP 约束

这轮只能宣称 `single-account available`，原因是：

- 当前 relay direct bridge 发送 bot-mailbox ingress mail 时使用的是全局 `taskmail_direct_from_addr`
- Android 当前 `Project list` 结果读取是按用户选中的 sender account 收敛到对应本地邮箱

因此本轮真正成立的前提是：

- Android 当前选中的 sender account
- 与 relay 上配置的 `taskmail_direct_from_addr`
- 指向同一个真实邮箱身份

若不满足这一前提，Android 仍应 fallback 到当前 mail `[SYNC]` 路径，而不是把 repo-side direct `[SYNC]` 误读成多账号已完成。

## 推荐代码落点

建议优先修改：

- `mail_runner/relay_server/app.py`
- `mail_runner/relay_server/direct_actions.py`
- 如需保持职责清晰，也可新增：
  - `mail_runner/relay_server/bootstrap_actions.py`

推荐测试落点：

- `tests/test_relay_server_app.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- `tests/test_app_phase2.py`

第一轮不建议修改：

- `docs/current/mail_protocol.md`
- `docs/current/android_runner_communication_contract.md`

原因是当前运行时行为还没有变化；行为变更落地后再升级 Layer 1 文档。

## 测试要求

至少补齐以下覆盖：

1. runtime 注册：
   - 开启 `taskmail_direct_ingress_enabled` 时，新 `[SYNC]` bridge handler 会注册到 relay runtime
2. direct bridge accepted：
   - direct `[SYNC]` packet 被接受后会发送 canonical `[SYNC]` mail 到 bot mailbox
   - `packet_ack.accepted=true` 且带 `transport_message_id`
3. direct bridge fallback-classified rejection：
   - bot mailbox bridge 临时不可用时，返回 fallback-classified rejection，而不是 hard reject
4. invalid payload / wrong schema：
   - 返回 hard rejection
5. idempotent replay：
   - 同一 `packet_id` 重放时，`receipt_id` 与 `transport_message_id` 保持稳定
   - 不重复桥接第二封 canonical `[SYNC]` ingress mail
6. mail semantics regression：
   - relay bridge 触发后，PC 侧最终仍生成 canonical `[SYNC] Project Folder List`
   - 仍不创建 task/thread/session

## 验收标准

repo-side 可以判定完成的条件：

1. Android 发来的 direct `[SYNC]` packet 在 relay 上得到 `accepted=true`
2. bot mailbox 中能看到 canonical `[SYNC]` ingress mail，且 ack 带该 ingress mail 的 `transport_message_id`
3. PC runtime 继续输出 canonical `[SYNC] Project Folder List`
4. `[SYNC]` 结果仍不进入 task/session 投影
5. 针对同一 direct `[SYNC]`，Android 侧不需要读取新的 relay-native 结果面
6. 对 relay-level retry 的同一 `packet_id`，repo-side 不会重复生成第二封 ingress mail

## 风险与注意事项

- 不要把这条 issue 扩写成“把 `[SYNC]` 全链路改成 direct-only”；mail result 仍是 truth layer
- 不要把 Android 本地 `sender_account_uuid` 当作服务端可依赖身份
- 不要在这条 issue 里顺手做多账号能力协商；那是后续单独切片
- 不要破坏当前 `[SYNC]` pruning 规则
- 不要让 `[SYNC]` 借这次 direct 化悄悄进入 task/session 状态模型

## 建议实施顺序

1. 先在 relay runtime 加 direct `[SYNC]` bridge 和测试
2. 再做 repo-side live loop / mailbox 语义回归确认
3. 最后再由 Android 把 `[SYNC]` 请求切到 `direct-first / mail-fallback`

## 直接可抄到 issue tracker 的短版标题

`TaskMail [SYNC] single-account MVP: accept relay direct request and bridge into canonical [SYNC] mail ingress`
