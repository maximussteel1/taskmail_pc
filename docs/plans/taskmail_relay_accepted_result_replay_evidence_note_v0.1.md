# TaskMail Relay Accepted / Result Replay 证据说明（v0.1）

更新时间：2026-03-23

## 状态

- 本文定义 `mail_based_task_manager` 仓当前对 relay-side accepted continuity 与 result replay continuity 的 repo-side 证据读法。
- 本文不声称 vNext `/control` 已经全面落地；它的作用是明确：现在哪些 machine-readable artifact 已能证明 continuity seam 存在，哪些仍只是后续 gap。
- 本文优先服务于 planning、closeout 与后续 `/control` 实现，不改写 `docs/current/*` current truth。

## Read First

- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `docs/plans/taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_bootstrap_control_contract_v2.md`
- `docs/plans/vps_relay_bootstrap_plan.md`
- `mail_runner/relay_server/packet_store.py`
- `mail_runner/relay_server/direct_actions.py`
- `tests/test_relay_server_packet_store.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- `docs/current/README.md`

## 1. 一句话结论

repo-side 当前已经有一条可审阅的 continuity seam：

- accepted continuity
  - 由 `PersistentAcceptedPacketStore` 的 `packets.json` 与 `delivery_attempts.jsonl` 提供
- bootstrap v2 result replay continuity
  - 由 `packets.json` 中的 `server_messages` 持久化结果提供

这还不是“所有 payload 的终态 result store 已冻结”，但已经足够证明 repo-side 不是从零开始。

## 2. 当前证据目标

本文关心的不是业务成功本身，而是三件事：

1. 同一 `packet_id` 是否稳定对应同一 `receipt_id`
2. accepted 后的最终 result 是否可稳定重放同一 `result_id`
3. 这些 continuity 是否有 machine-readable 落盘证据，而不是只靠人工日志

## 3. 当前 repo-side 证据源

### 3.1 accepted store

当前 repo-side 已有：

- `state_dir/packets.json`
- `state_dir/delivery_attempts.jsonl`

它们来自：

- `mail_runner/relay_server/packet_store.py`

当前 `AcceptedRelayPacket` 已持久化的关键字段包括：

- `packet_id`
- `receipt_id`
- `received_at`
- `delivery_status`
- `transport_message_id`
- `delivered_at`
- `last_error_code`
- `last_error_message`
- `attempt_count`
- `server_messages`

这表示 repo-side 已经具备：

- accepted continuity 的 durable 锚点
- delivery 结果的 durable 锚点
- result replay 消息的持久化挂载位

### 3.2 bootstrap v2 result replay

当前 repo-side 在 bootstrap v2 accepted path 上，已经把 relay result message 写进：

- `AcceptedRelayPacket.server_messages`

现有 direct action seam：

- `mail_runner/relay_server/direct_actions.py`
  - accepted `[SYNC]` v2 时构造 `bootstrap_result`
  - `result_id = bootstrap-result:<request_id>`
  - 通过 `server_messages=[result_message]` 交回 packet store 持久化

### 3.3 closeout / supporting evidence

repo-side 当前还有一层 supporting evidence：

- `runs/<task_id>/canonical_summary.json`
- `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json`
- `runs/<task_id>/taskmail_daily_closeout_bundle.json`

这些不是 relay-side replay authority 本身，但可作为：

- business outcome supporting evidence
- `request_id / packet_id / receipt_id` 对账锚点
- accepted 后 user-visible outcome 的补充证据

## 4. 当前已被代码与测试证明的事实

### 4.1 同一 `packet_id` 的 accepted continuity 已有测试

`tests/test_relay_server_packet_store.py` 已证明：

- 重复接受同一 `packet_id` 时，store 会保留第一次 `receipt_id`
- 不会因为重发而换出第二个 `receipt_id`

这说明 repo-side 当前已经有：

- `packet_id -> receipt_id` 的 idempotent seam

### 4.2 accepted continuity 可跨重启保留

`tests/test_relay_server_packet_store.py` 还证明：

- `PersistentAcceptedPacketStore` 在 reload 后仍能读回：
  - 同一 packet
  - 同一 delivery status
  - 同一 transport message id
  - 同一 attempt count

这说明 repo-side 当前不是“只靠进程内存撑 continuity”。

### 4.3 失败分类会持久化到 packet store

现有测试还证明：

- failed delivery 会把 `last_error_code` 与 `last_error_message` 写回 packet store

这对后续 vNext `/control` 的意义是：

- accepted 前 rejection / failure 与 accepted 后 delivery failure 至少已有 machine-readable readout 起点

### 4.4 bootstrap v2 的 result replay 已有正样本

`tests/test_relay_server_direct_actions.py` 与 `tests/test_relay_server_runtime.py` 已证明：

- accepted 的 bootstrap v2 请求会返回 `bootstrap_result`
- replay 同一 packet 时会返回同一 `receipt_id`
- replay 同一 packet 时会返回同一 `result_id`
- `packets.json` 持久化的 `server_messages` 中包含对应 result message

因此 repo-side 当前已不是“只有 ack continuity，没有 result continuity”。

## 5. 当前证据还没有证明什么

repo-side 当前还不能把以下事情说成“已经冻结完成”：

- generic `/control` result store 已覆盖全部 payload family
- 所有 future `event / result` 都已有独立 result journal
- packet store 的 file-backed 写入已经满足 vNext 全部 crash-consistency 要求
- post-creation direct action 已经全面接入 relay-side accepted/result replay continuity

当前更准确的读法是：

- continuity seam 已有
- bootstrap v2 正样本已有
- 更广 payload family 仍需继续补证据

## 6. 证据消费顺序

repo-side 当前建议按以下顺序消费 accepted/result replay 证据：

1. 先读 shared contract / companion note
2. 再读 relay-side `packets.json`
3. 再读 relay-side `delivery_attempts.jsonl`
4. 再读 packet 内 `server_messages`
5. 最后才读 `canonical_summary.json`、`session_action_closeout.json`、`taskmail_daily_closeout_bundle.json`

原因：

- accepted/result replay authority 首先属于 relay-side durable store
- closeout bundle 属于 supporting evidence，不是 accepted continuity 的第一事实源

## 7. repo-side 推荐证据字段

对后续更通用的 vNext `/control`，repo-side 至少应保证以下字段可被 machine-read：

- `packet_id`
- `receipt_id`
- `request_id`
- `result_id`
- `delivery_status`
- `last_error_code`
- `last_error_message`
- `sent_at`
- `server_messages`

如果 payload 已知 `trace_id / probe_id / related`，也建议随 result message 一并保留，而不是只保留裸业务体。

## 8. 下一步建议

repo-side 后续最合理的推进不是立刻再造一套全新 result DB，而是：

1. 先把 bootstrap v2 现有 continuity seam 明确登记为 repo-side evidence baseline
2. 再决定 future payload 是继续复用 `server_messages`，还是拆独立 result store
3. 在更广 payload family 接入时，先补 replay continuity 测试，再谈 Android-side cutover
4. 把 closeout bundle 对 relay-side replay evidence 的读取顺序固定下来

## 9. Merge Gate

在把 relay-side accepted/result continuity 当成 vNext `/control` 正式依赖前，至少应继续补齐：

1. 至少一个非 bootstrap payload 的 stable `result_id` replay 样本
2. accepted 后连接丢失再重连 replay 的 repo-side 证据
3. result continuity 与 closeout evidence 之间的固定读取顺序
4. 更明确的 crash-consistency 说明，而不是只靠“当前能 reload”

## 10. 当前结论

repo-side 当前已经可以明确说：

- `packet_id -> receipt_id` continuity 有 durable store 与测试证据
- bootstrap v2 的 `result_id` replay 有 durable store 与测试证据
- `packets.json` + `delivery_attempts.jsonl` + `server_messages` 已经构成 accepted/result replay 的第一批 machine-readable evidence

但更广的 shared `/control` result continuity 仍需在后续 payload 上继续补证据，而不是现在就宣称“问题已经完全关闭”。
