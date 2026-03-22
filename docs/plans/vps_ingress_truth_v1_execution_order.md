# VPS Ingress Truth V1 实施顺序

## Status

- Date: 2026-03-23
- Scope: `vps_ingress_truth_v1_checklist.md` 的第一版仓库实施顺序、模块分解与内部消息建议
- Layer: Layer 2 repository plan
- Related docs:
  - `docs/plans/vps_ingress_truth_v1_checklist.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/plans/vps_relay_bootstrap_plan.md`
  - `docs/plans/vps_relay_deploy_runbook.md`

## 1. 目标

这份文档不再重复解释“为什么要做 VPS ingress truth”。

它只回答三件事：

1. 第一批代码应该按什么顺序改
2. 第一批应该改哪些模块
3. 在当前 relay 包络下，建议增加哪些 repo-internal action

本实施顺序以最小风险为原则：

- 不改 Android Layer 1 合同
- 不改 mail-first reply/control 语义
- 不把执行迁去 VPS
- 不发明第二套 transport envelope

## 2. 固定约束

V1 实施时必须坚持以下约束：

- 继续复用当前 `/relay` + `hello` + `packet` + `packet_ack` 包络
- 新能力只作为 **repo-internal PC <-> VPS** 语义扩展
- 不让 Android 在 V1 依赖这些新 action
- 不把 `thread_state/session_state/tasks/` 直接改成 VPS 主库
- lease 与 ingress decision 必须可审计、可拒绝、可 fencing

## 3. 建议的传输复用方式

V1 不建议新增新的 WebSocket `message_type`。

建议继续沿用当前格式：

- client -> server：`message_type = "packet"`
- server -> client：`message_type = "packet_ack"` 或 `message_type = "error"`

只扩展 `task_run_packet` 和 `dispatch_metadata` 中的 repo-internal语义。

建议常量：

- `task_run_packet.schema_version = "vps-ingress-truth-v1"`
- `dispatch_metadata.schema_version = "vps-ingress-truth-v1"`
- `dispatch_metadata.channel = "pc_ingress_truth"`
- `dispatch_metadata.origin_client = "pc_mail_runner"`

建议 action 名：

- `acquire_mailbox_lease`
- `renew_mailbox_lease`
- `release_mailbox_lease`
- `register_ingress_candidate`
- `commit_thread_binding`
- `commit_terminal_outcome`

V1 的查询类能力先不走 `/relay` action。
operator 查询更适合先做成 VPS 侧本地 CLI / HTTP 只读诊断入口，避免过早扩张 runtime wire。

## 4. 第一批模块分解

### 4.1 Relay Server 侧

建议新增四个 store 模块：

- `mail_runner/relay_server/mailbox_lease_store.py`
- `mail_runner/relay_server/ingress_ledger_store.py`
- `mail_runner/relay_server/thread_binding_store.py`
- `mail_runner/relay_server/terminal_outcome_store.py`

建议新增一个 repo-internal handler 模块：

- `mail_runner/relay_server/ingress_truth_actions.py`

建议修改的现有模块：

- `mail_runner/relay_server/config.py`
- `mail_runner/relay_server/app.py`
- `mail_runner/relay_server/loopback.py`
- `mail_runner/relay_server/packet_store.py`
- `mail_runner/relay_server/protocol.py`

### 4.2 PC Runner 侧

建议新增一个轻量 client 模块：

- `mail_runner/ingress_truth_client.py`

建议修改的现有模块：

- `mail_runner/host.py`
- `mail_runner/app.py`
- `mail_runner/mail_io.py`
- `mail_runner/config.py`

V1 不建议把 IMAP fetch 逻辑整体搬走。
PC 仍负责实际取信；VPS 负责裁决“这封是否允许成为 canonical ingress”。

### 4.3 测试侧

建议新增：

- `tests/test_relay_server_ingress_truth_protocol.py`
- `tests/test_relay_server_ingress_truth_stores.py`
- `tests/test_ingress_truth_client.py`
- `tests/test_host_mailbox_lease.py`
- `tests/test_app_ingress_truth_gate.py`

## 5. 推荐实施阶段

V1 推荐分 5 个 patch set。

### Patch Set 0: 常量与文档冻结

目标：

- 冻结 schema/version/channel/action 命名
- 冻结 lease / ledger / binding / outcome 的最小字段集
- 不改任何 runtime 行为

本阶段改动：

- 文档
- 协议常量
- 空 store skeleton
- 协议 builder / parser skeleton

验收：

- 文档可 review
- 没有行为变化

### Patch Set 1: VPS Stores + Internal Packet Handler

目标：

- 先把 VPS 侧“能存、能拒绝、能审计”做出来
- 仍不影响 PC 当前 consume 行为

本阶段落地：

- `mailbox_lease_store.py`
- `ingress_ledger_store.py`
- `thread_binding_store.py`
- `terminal_outcome_store.py`
- `ingress_truth_actions.py`
- `loopback.py` 内部 handler 注册

本阶段 action：

- `acquire_mailbox_lease`
- `renew_mailbox_lease`
- `release_mailbox_lease`
- `register_ingress_candidate`
- `commit_thread_binding`
- `commit_terminal_outcome`

验收：

- 本地 loopback/relay test 能覆盖 accept/reject/fencing
- 还没有改 PC host，不会影响现有系统

### Patch Set 2: Host Lease Integration

目标：

- 先解决“双机同时 consume”问题
- 先不动 canonical new-task accept/reject

本阶段落地：

- host 启动时 acquire lease
- host 运行中定期 renew lease
- host 退出时 best-effort release lease
- 非 lease holder 不启动 mailbox consume loop

建议实现点：

- `mail_runner/host.py` 负责 lease 生命周期
- `mail_runner/config.py` 增加必要配置：
  - `ingress_truth_enabled`
  - `ingress_truth_url`
  - `ingress_truth_token`
  - `ingress_truth_timeout_seconds`
  - `ingress_truth_mode: strict|degraded`

验收：

- 两台 PC 同时启动时，只有一台进入 consume loop
- 另一台进入 standby / observer 状态

### Patch Set 3: New Task Ingress Gate

目标：

- 让首封新任务在本地建 thread 前必须先过 VPS decision
- 真正解决切机重放

本阶段落地：

- 在 `app.py` 的新任务入口增加远端裁决调用
- `processed_messages.json` 保留，但降级为本地 cache
- `register_ingress_candidate` 返回：
  - `accepted`
  - `duplicate`
  - `stale`
  - `invalid`
  - `lease_denied`
  - `ignored`

建议插入点：

- `_process_mail(...)`
- `_process_new_task_mail(...)`

关键顺序：

1. fetch candidate mail
2. 本地完成基础分类与首封解析
3. 调 `register_ingress_candidate`
4. 只有 `accepted` 才允许 `_start_snapshot_run(...)`

验收：

- 新机器在空本地 mailbox cache 下不会重放旧首封任务
- 同一 `message_id` / `uid` 不会创建第二个 thread

### Patch Set 4: Thread Binding + Terminal Outcome Mirror

目标：

- 把“accepted 之后最后到底落到哪个 thread/task”也挂到 VPS
- 让切机/对账闭环形成

本阶段落地：

- 本地 thread 创建成功后调用 `commit_thread_binding`
- terminal status mail / canonical summary 落盘后调用 `commit_terminal_outcome`

建议插入点：

- `_start_snapshot_run(...)` 成功接受后的 callback
- terminal closeout / canonical summary 写入完成后的回调路径

验收：

- operator 能从 VPS 查到 `message_id -> thread_id`
- operator 能从 VPS 查到 `thread_id -> latest terminal outcome`

### Patch Set 5: Operator Visibility

目标：

- 让这套真相不是“写进 VPS 但没人看得见”

本阶段落地建议：

- VPS 侧只读 CLI：
  - `show-mailbox-lease`
  - `show-ingress <message_id|uid>`
  - `show-thread-binding <thread_id|message_id>`
  - `show-terminal-outcome <thread_id>`
- 或最小 HTTP 只读诊断端点：
  - `/healthz` 扩展示意统计
  - `/debug/lease`
  - `/debug/ingress`
  - `/debug/outcome`

V1 更推荐先做 CLI，再决定是否公开 HTTP 只读接口。

## 6. 建议的 action 形状

以下是建议的最小 payload 形状。
它们不是当前已实现协议，只是 V1 的仓库内建议。

### 6.1 `acquire_mailbox_lease`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "acquire_mailbox_lease",
  "lease": {
    "mailbox_key": "bot:sgjcc@qq.com:INBOX",
    "runner_id": "pc-home",
    "host_fingerprint": "host:xxxx",
    "runtime_fingerprint": "runtime:xxxx",
    "config_fingerprint": "config:xxxx",
    "requested_ttl_seconds": 90
  }
}
```

成功 `packet_ack` 建议补字段：

- `accepted = true`
- `lease_epoch`
- `lease_expires_at`
- `lease_holder_id`

失败 `packet_ack/error` 建议补字段：

- `accepted = false`
- `error_code = "lease_conflict"`
- `current_lease_holder_id`
- `current_lease_expires_at`

### 6.2 `renew_mailbox_lease`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "renew_mailbox_lease",
  "lease": {
    "mailbox_key": "bot:sgjcc@qq.com:INBOX",
    "runner_id": "pc-home",
    "lease_epoch": 12,
    "requested_ttl_seconds": 90
  }
}
```

失败时建议错误码：

- `lease_not_found`
- `lease_epoch_mismatch`
- `lease_holder_mismatch`

### 6.3 `release_mailbox_lease`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "release_mailbox_lease",
  "lease": {
    "mailbox_key": "bot:sgjcc@qq.com:INBOX",
    "runner_id": "pc-home",
    "lease_epoch": 12,
    "reason": "host_shutdown"
  }
}
```

V1 建议 `release` 采用 best-effort 幂等语义。

### 6.4 `register_ingress_candidate`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "register_ingress_candidate",
  "candidate": {
    "mailbox_key": "bot:sgjcc@qq.com:INBOX",
    "runner_id": "pc-home",
    "lease_epoch": 12,
    "folder": "INBOX",
    "uid_validity": "1",
    "uid": "2048",
    "message_id": "<root@example.com>",
    "in_reply_to": null,
    "references": [],
    "from_addr": "user@example.com",
    "subject": "[CX] Demo task",
    "subject_norm": "demo task",
    "raw_date": "2026-03-23T10:00:00+08:00",
    "observed_at": "2026-03-23T10:00:03+08:00",
    "classification": "new_task",
    "freshness_window_minutes": 60
  }
}
```

成功 `packet_ack` 建议补字段：

- `accepted = true`
- `decision = "accepted"`
- `ingress_id`

拒绝 `packet_ack` 建议补字段：

- `accepted = false`
- `decision`
- `decision_reason`
- `ingress_id`

V1 的标准 `decision` 值建议固定为：

- `accepted`
- `duplicate`
- `stale`
- `invalid`
- `ignored`
- `lease_denied`

### 6.5 `commit_thread_binding`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "commit_thread_binding",
  "binding": {
    "mailbox_key": "bot:sgjcc@qq.com:INBOX",
    "runner_id": "pc-home",
    "lease_epoch": 12,
    "ingress_id": "ingress_001",
    "root_message_id": "<root@example.com>",
    "thread_id": "thread_019",
    "session_id": "thread_019",
    "repo_path": "E:\\projects\\demo",
    "workdir": "src",
    "subject_norm": "demo task"
  }
}
```

建议失败码：

- `ingress_not_found`
- `binding_conflict`
- `lease_epoch_mismatch`

### 6.6 `commit_terminal_outcome`

`task_run_packet`:

```json
{
  "schema_version": "vps-ingress-truth-v1",
  "action": "commit_terminal_outcome",
  "outcome": {
    "runner_id": "pc-home",
    "thread_id": "thread_019",
    "task_id": "20260323_101500_abcd",
    "run_status": "success",
    "generated_at": "2026-03-23T10:20:00+08:00",
    "last_summary": "Implemented the requested change.",
    "terminal_mail_message_id": "<sent-3@example.com>",
    "terminal_mail_subject": "[DONE][S:thread_019] Demo task",
    "source_ingress_id": "ingress_001",
    "request_id": null,
    "packet_id": null
  }
}
```

V1 建议 outcome commit 允许覆盖同一 `thread_id + task_id` 的重复提交，但必须记录 `updated_at`。

## 7. Store 的最小接口建议

### 7.1 `MailboxLeaseStore`

建议接口：

- `acquire(...) -> LeaseGrant | LeaseConflict`
- `renew(...) -> LeaseGrant | LeaseRejected`
- `release(...) -> LeaseReleased`
- `get_active(mailbox_key) -> MailboxLease | None`

### 7.2 `IngressLedgerStore`

建议接口：

- `register_candidate(...) -> IngressDecision`
- `get_by_message_id(...)`
- `get_by_uid(...)`
- `list_recent(...)`

### 7.3 `ThreadBindingStore`

建议接口：

- `commit_binding(...)`
- `get_by_ingress_id(...)`
- `get_by_message_id(...)`
- `get_by_thread_id(...)`

### 7.4 `TerminalOutcomeStore`

建议接口：

- `commit_outcome(...)`
- `get_latest_for_thread(thread_id)`
- `get_for_task(thread_id, task_id)`

## 8. 对现有代码的插点建议

### 8.1 `mail_runner.host`

职责：

- host 启动时 acquire lease
- 周期 renew
- 失去 lease 时停止 consume loop

不建议把 lease 逻辑塞进 `mail_io.py`。
lease 是 host/operator 语义，不是 IMAP 语义。

### 8.2 `mail_runner.app`

职责：

- 在 `_process_mail(...)` 的首封新任务分支前后插入远端 decision
- 在 `_start_snapshot_run(...)` 接受成功后 commit thread binding
- 在 terminal closeout 路径 commit terminal outcome

### 8.3 `mail_runner.mail_io`

职责：

- 最多只保留本地 mailbox cache 的辅助读写
- 不再承担 canonical accept/reject 决策

## 9. 测试顺序建议

推荐严格按下面顺序补测试：

1. store 单测
2. protocol builder/parser 单测
3. relay loopback handler 单测
4. ingress_truth_client 单测
5. host lease 行为测试
6. app 首封 decision gate 测试
7. end-to-end 切机 smoke

不要一上来就写跨进程 live test。
先把 accept/reject/fencing 语义在纯单测里钉死。

## 10. 风险排序

V1 最先要防的风险顺序建议是：

1. 双机同时 consume
2. 新机器重放旧首封任务
3. accepted 后无法做 canonical binding
4. terminal outcome 无法对账
5. operator 看不到 VPS 侧真实状态

也就是说：

- lease 要先于 binding/outcome 落地
- ingress decision gate 要先于 operator fancy UI 落地

## 11. 明确不建议的实现路径

V1 不建议：

- 直接把 `processed_messages.json` rsync 到 VPS 当真相
- 用共享磁盘代替 lease/ledger 协议
- 让 VPS 直接接 IMAP 然后再通知 PC
- 在没有 fencing token 的情况下只靠“最后写入 wins”
- 把 reply/control 一起塞进第一批 patch set
- 在 V1 就引入 Android 可见的新业务 endpoint

## 12. 第一轮完成定义

当以下事项全部完成时，可认为 `VPS ingress truth v1` 第一轮落地完成：

- lease 已接管“谁能 consume bot mailbox”
- 首封新任务 accept/reject 已由 VPS 决定
- 本地空 mailbox cache 不再导致旧任务重放
- accepted 首封能在 VPS 查到 canonical `thread_id/session_id`
- terminal outcome 能在 VPS 查到镜像记录
- 当前 mail-first reply/control 与 direct detail sidecar 行为无回归

## 13. 下一步建议

如果按这个顺序继续推进，建议下一次真正开始写代码时只做一个窄 slice：

1. 先落 `MailboxLeaseStore`
2. 再落 `acquire/renew/release_mailbox_lease`
3. 最后只把 host consume 权切到 lease 上

也就是：

- **第一刀先解决“双机同时收件”**
- 暂时不碰 `register_ingress_candidate`
- 暂时不碰 binding/outcome

这是风险最低、最容易快速看到收益的切入点。
