# VPS Ingress Truth V1 设计清单

## Status

- Date: 2026-03-23
- Scope: 当前仓库内“把 ingress / 切机协调真相迁入 VPS，但不迁移执行真相”的第一版设计与实施清单
- Layer: Layer 2 repository plan
- Related docs:
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/plans/vps_relay_bootstrap_plan.md`
  - `docs/plans/vps_relay_deploy_runbook.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`

## Repo-side Freeze

- Date: 2026-03-26
- Decision: 本仓库的 V1 实现统一挂在当前 `/pc-control` websocket 上，不新增一条临时 repo-internal HTTP 面

本轮冻结的 repo-side 口径如下：

- wire type 固定为：
  - client -> server:
    - `mailbox_lease`
    - `ingress_candidate`
    - `thread_binding`
    - `terminal_outcome`
  - server -> client:
    - `mailbox_lease_ack`
    - `ingress_decision`
    - `thread_binding_ack`
    - `terminal_outcome_ack`
- `mailbox_lease.payload.operation` 固定为：
  - `acquire`
  - `renew`
  - `release`
- PC 侧新增配置：
  - `relay_mailbox_lease_mode = disabled | strict | degraded`
  - `relay_mailbox_lease_ttl_seconds`
- repo 默认保持 `relay_mailbox_lease_mode=disabled`，避免未部署 relay V1 时静默切换当前 mail-first 行为
- 当 operator 显式开启 `strict|degraded` 时：
  - host 只在 sidecar 已拿到 active lease 时才允许 consume mailbox
  - `strict` 下，sidecar 未连上或 lease 丢失时，host 不消费新邮件
  - `degraded` 下，sidecar 未连上或 lease 丢失时，host 允许继续本地消费，但 ingress / closeout 都要带 `degraded_mode=true`
- `mailbox_key` 统一由当前 bot mailbox receive identity 派生：
  - `imap_host`
  - `imap_user`
  - mailbox 固定 `INBOX`
- 首封新任务的 canonical accept path 固定为：
  1. PC fetch 到候选首封新任务 mail
  2. PC 通过 `ingress_candidate` 先拿 VPS decision
  3. 只有 `decision=accepted` 才允许本地创建 thread/session
  4. 本地 thread 创建成功后，再提交 `thread_binding`
  5. terminal closeout 时，再提交 `terminal_outcome`
- 远端持久化层固定收敛为一套 `ingress_truth` store，内部至少保存：
  - mailbox lease
  - lease history / transfer timeline
  - ingress ledger
  - canonical thread binding
  - terminal outcome journal
- fencing 规则固定为：
  - `ingress_candidate` / `thread_binding` 必须命中当前 active lease，且 `lease_epoch` 必须等于 VPS current epoch
  - `terminal_outcome` 也必须带 `lease_epoch`
  - 如果当前 lease 已被更高 epoch holder 接管，旧 holder 的后续 ingress / binding / outcome 写入一律返回 `lease_denied`
- 为了保证去重和 operator 可解释性，PC 在 fetch 邮件时会把 IMAP `uid` 带入 `MailEnvelope`；`uid_validity` 保持 best-effort，可为空
- 最小 operator read-side 固定包含：
  - 当前 `mailbox_key` 的 lease holder / epoch / expires_at
  - 通过 `message_id` 或 `uid` 查询 ingress decision
  - 通过 `thread_id` 查询 terminal outcome

## 1. 目标

本设计要解决的不是“把整个系统唯一真相迁去 VPS”，而是把**最容易在切机时出错的 ingress / 接管协调真相**迁去 VPS。

本设计的直接背景是：

- 当前 bot mailbox 的已消费/去重状态主要落在本地 `task_root/_mailbox/processed_messages.json`
- 当前 host 锁是本机 `runtime_dir` 级别，不是跨机器锁
- 切换电脑、切换 `task_root`、或本地运行态丢失时，旧首封任务邮件可能被重新当成新任务消费
- 这类问题的根因不是执行逻辑本身，而是 ingress continuity truth 仍然是本地状态

V1 的目标是：

- 让 VPS 成为 bot mailbox ingress 去重与接管协调的唯一真相
- 保持 PC 仍然是 task execution truth
- 避免“换电脑后旧邮件重放成真实 backend run”
- 为后续 Android / PC / VPS 协同开发提供稳定的切机与接管基础

## 2. 设计结论

V1 采用以下边界：

- VPS 持有 `ingress truth`
- PC 持有 `execution truth`
- mail 继续是 user-visible receipt / history / attachment truth
- Phase 3 direct detail 继续只是 read-side freshness projection，不成为 execution truth

更具体地说：

- **VPS 真相**
  - bot mailbox ingress ledger
  - 去重结果
  - 首封任务是否被接受/跳过/拒绝
  - 当前 lease 持有者
  - 首封任务到 `thread_id/session_id` 的 canonical 绑定
  - terminal outcome journal 的远端副本
- **PC 真相**
  - repo / worktree
  - backend 进程与 native session
  - `tasks/` 下的 run artifacts / logs / snapshots
  - 本地 monitor / operator intervention / high-permission execution

V1 明确**不**尝试让 VPS 变成完整唯一真相。

## 3. V1 非目标

以下内容不在 V1 范围内：

- 不把 `codex` / `opencode` 执行迁到 VPS
- 不把 repo checkout / worktree / `tasks/` 整体迁到 VPS
- 不让 VPS 成为 `thread_state.json` / `session_state.json` 的完整主库
- 不让 Android 直接改走“VPS 业务 API”作为默认控制面
- 不替换 mail receipt / attachment / history truth
- 不支持多台 PC active-active 同时执行同一 bot mailbox
- 不在 V1 解决“执行中 native session 跨机器恢复”

## 4. V1 成功标准

V1 达标应同时满足以下条件：

- 切换到一台新 PC 且本地 `task_root/_mailbox` 为空时，不会重放历史首封任务
- 同一时刻最多只有一台 PC 拥有 bot mailbox 的 active ingress lease
- operator 可以在 VPS 上解释任意一封首封任务为什么被接受、跳过、或拒绝
- PC 重启或短时离线后，只要 lease 未转移，不会重复创建同一任务
- lease 转移后，新 PC 可以继续消费新邮件，而不会因为本地缺状态重吃旧邮件
- 现有 mail-first / relay / Phase 3 detail sidecar 边界不被破坏

## 5. 真相对象

V1 至少需要四类 VPS 侧对象。

### 5.1 Ingress Ledger

每个进入 bot mailbox 的候选 ingress mail 都要有一条 ledger 记录。

建议字段：

- `ingress_id`
- `mailbox_key`
- `folder`
- `uid_validity`
- `uid`
- `message_id`
- `in_reply_to`
- `references_hash`
- `from_addr`
- `subject`
- `subject_norm`
- `raw_date`
- `observed_at`
- `classification`
- `dedupe_key_uid`
- `dedupe_key_message_id`
- `decision`
- `decision_reason`
- `lease_holder_id`
- `lease_epoch`
- `thread_id`
- `session_id`
- `request_id`
- `packet_id`
- `accepted_at`
- `closed_at`

其中：

- `classification` 只描述 ingress 视角：
  - `new_task`
  - `reply`
  - `sync`
  - `direct_kill`
  - `system_mail`
  - `unsupported`
- `decision` 只描述 VPS 作为 ingress truth 的裁决：
  - `accepted`
  - `duplicate`
  - `stale`
  - `invalid`
  - `ignored`
  - `lease_denied`

### 5.2 Mailbox Lease

V1 需要一个显式的 mailbox lease 记录，替代“本机 runtime_dir 锁就是唯一锁”的现状。

建议字段：

- `mailbox_key`
- `lease_holder_id`
- `lease_epoch`
- `status`
- `acquired_at`
- `renewed_at`
- `expires_at`
- `config_fingerprint`
- `host_fingerprint`
- `runtime_fingerprint`
- `last_seen_thread_id`
- `last_seen_ingress_id`

规则：

- 同一 `mailbox_key` 在同一时刻只允许一个 `active` lease
- `lease_epoch` 必须单调递增，用作 fencing token
- 任何 ingest / commit / closeout 写入都必须带 `lease_epoch`
- 旧 lease holder 即使“活着”，只要 epoch 落后，也不能继续提交 ingress 决策

### 5.3 Canonical Thread Binding

V1 需要在 VPS 上保存“首封 ingress mail -> 本地 thread/session 身份”的 canonical 绑定。

建议字段：

- `ingress_id`
- `root_message_id`
- `thread_id`
- `session_id`
- `repo_path`
- `workdir`
- `subject_norm`
- `binding_created_at`

这层的作用不是接管本地完整 session state，而是回答：

- 这封首封任务最终对应哪个 `thread/session`
- 后续 closeout / 对账该挂到哪里
- 新机器接管时，哪些首封已经 canonicalized，不能再二次创建

### 5.4 Terminal Outcome Journal

V1 还需要一个轻量 terminal outcome journal，作为本地 `canonical_summary.json` 的远端镜像。

建议字段：

- `thread_id`
- `task_id`
- `run_status`
- `generated_at`
- `last_summary`
- `terminal_mail_message_id`
- `terminal_mail_subject`
- `request_id`
- `packet_id`
- `source_ingress_id`

这层的目标不是替代本地 artifact truth，而是：

- 让切机后能快速知道“这条线程上次已经跑到哪里”
- 让 operator 在 VPS 侧做 ingress / terminal 对账
- 为后续 Android / relay closeout 提供稳定锚点

## 6. 核心协议边界

V1 的关键在于：**PC 在真正创建本地 thread 之前，先向 VPS 取得 ingress 裁决。**

建议顺序如下：

1. PC 启动时向 VPS 申请 `mailbox lease`
2. 只有 lease holder 才允许执行 IMAP ingress consume
3. PC 从 IMAP 取到候选邮件后，先向 VPS 提交 `register_ingress_candidate`
4. VPS 依据 ledger + 当前 lease 返回裁决
5. 只有 `decision=accepted` 的首封新任务才允许本地创建 `thread/session`
6. 本地创建成功后，PC 再向 VPS 提交 `commit_thread_binding`
7. terminal closeout 时，PC 再向 VPS 提交 `commit_terminal_outcome`

reply / `[SYNC]` / `[KILL]` 在 V1 里也可以写 ledger，但重点仍是首封任务 ingress。

## 7. 决策规则

V1 建议固定以下决策规则。

### 7.1 首封新任务

当一封邮件被分类为非 reply `[OC]` / `[CX]` 首封任务时：

- 若 `(mailbox_key, uid_validity, uid)` 已出现，返回 `duplicate`
- 若 `message_id` 已被另一条 canonical ingress 占用，返回 `duplicate`
- 若本地提交的 `lease_epoch` 不是当前有效 lease，返回 `lease_denied`
- 若命中 `new_task_max_age_minutes` 等 freshness gate，返回 `stale`
- 若正文不满足首封任务解析要求，返回 `invalid`
- 只有全部通过时，返回 `accepted`

### 7.2 reply / control mail

reply/control 在 V1 不必全部强制走 VPS 决策，但建议至少写入 ledger，方便后续演进。

建议：

- reply mail 先记账，不阻断本地现有解析
- `/status` / `/pause` / `/resume` 等仍保持当前 mail-first 本地处理
- direct post-creation action 仍按现有计划文档单独推进，不混入本设计

### 7.3 lease 失效

lease holder 失效时：

- 新 PC 可获取更高 `lease_epoch`
- 旧 PC 后续所有 ingress 提交必须被 fencing 拒绝
- 旧 PC 即使本地还在跑，也只能完成本地执行 closeout，不能继续领取新 ingress

## 8. 运行模式

V1 推荐明确区分两种 PC 运行模式。

### 8.1 Lease Holder

具备：

- IMAP consume 权
- ingress candidate 提交权
- thread binding commit 权
- terminal outcome commit 权

### 8.2 Observer / Standby

具备：

- 读取 VPS ledger / outcome 能力
- 本地 `observe` / 日志查看能力
- 不具备 consume 新邮件权

这样切机时，新机器不需要“先本地跑起来试试”，而是先成为 `standby`，再显式抢占 lease。

## 9. 本地状态的新定位

V1 落地后，本地这些状态不再是唯一真相：

- `task_root/_mailbox/processed_messages.json`
- 本地 `runtime_dir` 下的 host lock
- 本地“我觉得这封没处理过”的判断

它们的新定位应改为：

- 本地缓存
- 本地加速索引
- 本地容错副本

不能再作为是否创建首封任务的最终裁决。

## 10. 对当前仓库的最小改造点

V1 在仓库内的最小改造建议如下。

### 10.1 VPS / relay 侧

- 在 relay server 下增加持久化 ingress ledger store
- 增加 mailbox lease store
- 增加 canonical thread binding store
- 增加 terminal outcome journal store
- 在 `/relay` 或新的 repo-internal transport seam 上增加最小消息类型：
  - `acquire_mailbox_lease`
  - `renew_mailbox_lease`
  - `release_mailbox_lease`
  - `register_ingress_candidate`
  - `commit_thread_binding`
  - `commit_terminal_outcome`

### 10.2 PC runner 侧

- host 启动时先 acquire / renew lease
- 非 lease holder 不允许启动 mailbox consume loop
- 新任务入口改成“先远端裁决，后本地建 thread”
- `processed_messages.json` 降级为 cache，不再决定 canonical accept/reject
- closeout 时把本地 `canonical_summary.json` 镜像提交到 VPS

### 10.3 Observe / operator 侧

- 新增 operator 观察入口：
  - 当前 mailbox lease holder
  - 最近 ingress decisions
  - 某封 `message_id` 的 canonical decision
  - 某个 `thread_id` 的远端 terminal outcome

## 11. 迁移策略

V1 不应“一步切换”。

推荐四步迁移：

### Step A: 只记账，不裁决

- VPS 先接 ingress ledger 与 terminal outcome journal
- PC 仍按当前本地逻辑消费
- 目的：先验证记录模型和字段是否足够

### Step B: 引入 lease，但不阻断本地 cache

- PC consume loop 需要 lease 才能启动
- 但是否 accepted 仍可先对照本地 cache
- 目的：先解决双机同时收件

### Step C: 首封新任务改为 VPS 裁决

- `new_task` accept/reject 由 VPS ledger 决定
- 本地 cache 只做加速
- 目的：解决切机重放

### Step D: canonical binding / outcome 对账闭环

- terminal outcome 远端化
- operator 可以在 VPS 上完成 ingress 到 terminal 的闭环解释

## 12. 失败与回退

V1 需要显式定义失败时怎么退。

建议规则：

- VPS 不可达时，lease holder 不应自动退回“本地继续真相模式”
- operator 必须显式选择：
  - `strict`: 没有 VPS 就不 consume 新邮件
  - `degraded`: 临时允许本地 consume，但所有结果打上 `degraded_mode=true`
- 默认建议 `strict`

原因：

- 如果 VPS 不可达时又自动回退到本地 canonical accept/reject，V1 的唯一 ingress truth 就会失效
- 这种“静默双真相”比短时不可用更危险

## 13. 安全与审计要求

V1 至少需要以下审计能力：

- 每条 ingress decision 可按 `message_id` 或 `uid` 查询
- 每次 lease 转移可按时间线回放
- 每条 terminal outcome 能回溯到 source ingress
- 所有写操作都记录 `runner_id` 与 `lease_epoch`

同时保持：

- token/credential 不入库源码
- 不把高权限执行能力上移到 VPS
- 不让 Android 获得通用 ingress truth 写权限

## 14. 验收清单

- [ ] VPS 侧能持久化 ingress ledger
- [ ] VPS 侧能持久化 mailbox lease
- [ ] VPS 侧能持久化 canonical thread binding
- [ ] VPS 侧能持久化 terminal outcome journal
- [ ] PC 启动时能 acquire / renew / release lease
- [ ] 非 lease holder 不能 consume bot mailbox
- [ ] 首封新任务在本地建 thread 前必须先拿到 VPS accept
- [ ] 新机器切换后，本地空 `processed_messages.json` 不会重放旧首封任务
- [ ] operator 能查询某封首封邮件为何被 `accepted / duplicate / stale / invalid`
- [ ] operator 能查询当前 lease holder 与最近一次转移
- [ ] terminal outcome 能在 VPS 侧对齐到 `thread_id/task_id`
- [ ] mail-first 现有 reply/control 行为不回归

## 15. V1 后续出口

当 V1 完成后，后续可继续演进的方向是：

- V1.5: reply / control mail 也进入更完整的 VPS ledger
- V2: VPS 维护更完整的 session routing / continuity truth
- V3: 如果未来真的转向 remote-first execution，再单独讨论 execution truth 迁移

但在 V1 阶段，必须坚持一个边界：

- **不要把“direct-connect 演进”与“execution truth 迁移到 VPS”绑成同一个改造。**

这是本设计最重要的 guardrail。
