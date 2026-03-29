# Mail Protocol

## Status

- Date: 2026-03-25
- Scope: current `mail_based_task_manager` mail control plane
- Role: canonical current protocol document for task mail ingress, reply routing, and user-visible mail actions

## 1. Position

当前仓库是邮件驱动的任务执行适配层，不是完整 Task Manager 平台。

Mail 是当前 canonical 默认控制面，用于：

- 新任务创建
- 首封只读环境发现
- reply continuation
- question / answer recovery
- status 查询
- rerun / kill
- 当前 workspace 内的 session listing

补充说明：

- 当前系统已经存在窄范围 relay direct surface，但 mail 仍是默认入口与默认用户可见结果载体
- direct surface 的当前事实以 [taskmail_direct_control_file_contract.md](taskmail_direct_control_file_contract.md) 为准

### 1.1 Recommended Mailbox Topology

当前推荐使用双邮箱拓扑：

- `bot mailbox`：只给 runner 使用，负责 IMAP 收件和 SMTP 发件
- `user mailbox`：只给人或 Android 邮件客户端使用
- 用户始终从 `user mailbox` 发给 `bot mailbox`
- 系统始终从 `bot mailbox` 回复原发件人

当前收件端不再把 `UNSEEN` 当成唯一消费条件；runner 会按 `INBOX` 的 IMAP UID 增量扫描，并在本地按 `UID` + `Message-ID` 去重。
For the bot mailbox receive path, the host now also supports best-effort IMAP `IDLE` on servers that advertise it. `IDLE` is only used as a wake-up signal; the canonical fetch/dedupe path remains UID-based scanning. `IDLE` reads are bounded so a stalled long-lived socket cannot block the host loop indefinitely, and the host also forces a periodic full mailbox sync plus `IDLE` rebuild every 5 minutes; unsupported or unstable servers automatically fall back to polling.

### 1.2 Outbound Transport Modes

Current outbound delivery now supports two transport modes:

- `email`: the PC sends the user-facing status mail directly, which remains the default behavior
- `relay`: the PC sends one outbound packet to the VPS relay, the VPS persists relay/session continuity for restart recovery, and the VPS sends the user-facing status mail via its own SMTP path
- relay bootstrap and `healthz` probing now use a direct HTTP client path and do not inherit ambient `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` environment variables from the host process

Current relay boundary:

- relay continuity is durable on the VPS (`packet` history, delivery attempts, and session continuity survive relay restart)
- task execution truth still remains on the PC side
- the Android-facing mail contract does not change when relay is enabled
- when relay delivery fails and `relay_auto_fallback_email` is enabled, the PC may automatically fall back to direct `email` delivery in the same outbound flow

Current control-plane mode switch:

- `control_plane_mode=mail_first`: PC host keeps the legacy mailbox-driven control plane and does not start the `pc-control` sidecar
- `control_plane_mode=hybrid`: current default; PC host keeps mailbox ingress while also running the `pc-control` sidecar
- `control_plane_mode=vps_only`: PC host stops consuming the bot mailbox for control-plane ingress; mail may still remain as a user-visible notification/result transport, but old mailbox ingress is no longer the host-side control-plane entry

Current optional direct TaskMail boundary:

- relay 当前可接受窄范围 direct `new_task`
- relay 当前可接受 bootstrap `[SYNC]` `v1` / `v2`
- relay 当前还暴露 shared `/control` websocket；当前已落地 bootstrap `sync_project_folders` `v2`、current-session direct `/status` / plain `reply`，以及 relay-side `transport_probe`
- `/control` 与 `/relay`、`/v1/files` 当前复用同一 `Authorization: Bearer <transport_token>` 认证路径
- repo-side 还存在 operator-only debug `POST /debug/pc-control/dispatch`，以及 `GET /debug/pc-control/nodes`、`GET /debug/pc-control/workspaces`、`GET /debug/pc-control/commands` 等 read-side 入口；这些路径当前都复用同一 bearer token admission，只负责向 live `pc_control_runtime` 做 operator 级 dispatch / observe，不是新的 app-facing / user-facing API
- `2026-03-29` 起，repo-side 已把 repo-internal `pc_control` current-session session-action first slice 补到了 `reply|status|pause|resume|kill|end|answers|attachment_continuation`：operator/debug `command_dispatch(reply|status|pause|resume|kill|end|answers|attachment_continuation)` 现在都会在 PC 本地复用现有 post-creation mail path，并回传 `result.structured_payload.kind=session_action_result`。其中 `attachment_continuation` 复用现有 attachment-bearing reply mail 语义，把内联附件先物化进目标 workdir，再按当前 thread 状态继续走 canonical continuation / answer recovery 逻辑。这条能力当前应读成内部 canonical command-family seam，而不是新的 app-facing transport API
- `2026-03-29` 起，repo-side 还额外 provision 了一条 current-session session-action first slice 的 Android-facing facade：`POST /v1/android/session-action`。它当前承接 current-session `reply|status|pause|resume|kill|end|answers|attachment_continuation`，固定使用独立的 `Authorization: Bearer <android_app_token>` admission，并把 `request_id / action / target.session_id` 薄映射到内部 `pc_control_runtime -> command_dispatch(reply|status|pause|resume|kill|end|answers|attachment_continuation)`；`target.workspace_id` 与 `target.thread_id` 当前都只作 supporting identity / diagnostics。submit success/replay 当前返回 `command_id + submit_ack + target_session_identity`，其中 `pause/resume/kill/end` 当前要求空 object action body，`answers` 当前要求 `answers.question_answers[]`，而 `attachment_continuation` 当前要求 `attachment_continuation.attachments[]` 并支持可选 `reply_text`。当前 first accepted / `accepted_but_queued` / same-payload replay 固定返回 `HTTP 200`，同一 `request_id` 命中不同 canonical payload 固定返回 `HTTP 409 request_id_conflict`；当前 repo-side 已把 current-session canonical route resolve 收回服务端：`session_id` 可单独命中唯一 session，若 supporting identity 不一致则返回 conflict，若 `session_id` 多命中则返回 unresolved，而不是猜测。这条入口当前仍只应读成 current-session session-action first slice，不代表 Android 已退出 mail-first 主边界
- 对这条 `pc_control` dispatch 路径，当前显式 `execution_policy.profile=default` 与省略 profile 的语义等价；它不会再在本地 adapter bootstrap 阶段因为“缺少 default profile mapping”而被额外打断
- `/control` 首刀保留 `hello / hello_ack`，并在 `hello_ack` 里回告 `accepted_payload_schemas`
- `/control` 当前支持四类 frame 流：
  - `ping -> pong`
  - `command(status|reply) -> command_ack -> result(session_action_result)`
  - bootstrap `command(sync_project_folders) -> command_ack -> result(sync_project_folders_result)`
  - `transport_probe command -> command_ack -> event* -> result(transport_probe_result)`
- `/control` accepted/replay 当前复用 relay packet store；同一 `packet_id` replay 会返回同一 `receipt_id`，已物化的 `session_action_result` / bootstrap / `transport_probe` final result replay 会返回同一 `result_id`
- `transport_probe_result` 当前仍是 relay-side harness/debug result，但它已不再只证明 mail bridge submission
- relay 现在会在 mail 提交成功后按 `timeout_seconds` 等待 relay-visible `tasks/_mailbox/transport_probes/<probe_id>.json`：
- `status=completed` + `outcome=observed` 表示 relay 已读到与当前 `request_id/packet_id/trace_id/transport_message_id` 对齐的 PC mailbox observation
- `status=partial` + `outcome=timed_out` 表示 relay 已提交 probe mail，但在超时前未观测到匹配 evidence
- `status=partial` + `outcome=submitted` 表示 relay 已提交 probe mail，但当前 runtime 没有可用的 PC observation lookup 能力
- `status=failed` + `outcome=failed` 表示 relay 在 mail submission 阶段失败
- `transport_probe_result.payload.observation` 现在会携带 projected observation summary 或当前 wait/skip state；PC host 仍继续把原始 mailbox observation sidecar 写到 `tasks/_mailbox/transport_probes/<probe_id>.json`
- relay 当前可接受 current-session direct `/status` 与 plain `reply`
- 对 `post-creation-session-action-contract-v1`，`dispatch_metadata.fallback_policy` 当前接受 `mail` 与 `none`；`none` 只表示合法的 direct-only client 声明，不会在 parser 层因“不是 mail”而被直接拒绝
- 这些 direct surface 的 task execution truth 仍留在 PC，不把 relay 提升成执行真相层
- `/control session_action_result` 当前只回告 canonical mail ingress 已提交以及当前 `session_action_closeout` 锚点快照；user-visible final outcome 仍以正常 status / terminal mail 为准
- `GET /v1/android/session-snapshot` 当前还会在 `session_snapshot.latest_session_action` 下保守投影最近一条 current-session `reply|status|pause|resume|kill|end|answers|attachment_continuation` command 的最小 continuity 字段，至少包含 `command_id + action_type + submit_ack`
- direct `new_task` 与 current-session direct action 当前都走 bridge-to-mail；`/control` bootstrap `v2`、current-session direct `session_action_result` 与 relay-side `transport_probe` 是当前三类 direct result surface
- `/relay` 当前仍是 direct `new_task`、current-session direct `/status` / `reply` 与 Phase 3 detail sidecar 的 carrier；`/control` 现在也可承载 current-session direct `/status` / `reply`，但它仍不是这些能力的通用替代
- 具体 schema、限制条件、closeout/evidence 与 `/v1/files` file surface 规则，以 [taskmail_direct_control_file_contract.md](taskmail_direct_control_file_contract.md) 为准
- 当 `control_plane_mode=vps_only` 时，旧 mail-bridge control-plane surface 不再应读成被 provision：PC host 不再 consume bot mailbox，relay 侧依赖 bot mailbox ingress 的 direct `new_task` / current-session `status|reply` / bootstrap `[SYNC] v1` / `transport_probe` 也不再是 active lane
- 对 repo-internal `pc_control` sidecar，当前还存在一条可选的 VPS ingress-truth V1 能力：
  - PC 可在同一条 `/pc-control` websocket 上申请 bot mailbox lease，并把首封新任务 accept/reject 与 terminal closeout 镜像到 relay
  - 这条能力由 `relay_mailbox_lease_mode` 显式开启；repo 默认仍保持 `disabled`
  - `relay_mailbox_lease_mode=strict` 时，没有 active lease 就不 consume 新邮件
  - `relay_mailbox_lease_mode=degraded` 时，host 允许在 relay 不可达时继续本地 consume，但相关 ingress / closeout 会标记 `degraded_mode=true`
  - 这条能力只迁移 ingress / cutover coordination truth，不迁移执行真相；task execution truth 仍留在 PC

Current optional direct TaskMail active-detail sidecar boundary:

- when the relay operator provisions the current Phase 3 direct inbound wire, the relay may also accept `subscribe_session_detail` on `/relay`
- this direct path is read-side only and is limited to `active session detail` freshness (`session_snapshot` / `session_delta`)
- the relay resolves the current runtime `session_state` / `thread_state` and projects them into the frozen Phase 3 wire shape
- mail remains the receipt/artifact/attachment truth layer even when this sidecar is enabled
- direct detail sidecar 本身仍是 read-side only；其他 direct post-creation action 是否受支持，不由 sidecar 推导，而是由 [taskmail_direct_control_file_contract.md](taskmail_direct_control_file_contract.md) 单独定义

## 2. Authority

当文档发生冲突时，优先级如下：

1. `docs/current/` 下与当前行为直接对应的 canonical / 专题协议文档
2. `README.md` 与 `state.md` 中的当前状态描述
3. `docs/plans/*`
4. `docs/platform/*`
5. `docs/archive/*`

补充规则：
- 如本文件与 `docs/current/` 下的更具体专题协议文档冲突，以更具体的当前协议文档为准
- 如 `README.md`、`state.md` 与 `docs/current/*` 冲突，以 `docs/current/*` 为准

## 3. First-Mail Control Actions

当前支持的首封非 reply 控制动作有两类：

- 新任务创建：`[OC]` / `[CX]`
- 项目目录同步：`[SYNC]`

### 3.1 Project Folder Sync Mail

`[SYNC]` 是首封只读环境发现入口，用于帮助手机端或首次用户查看当前机器上可用的项目目录。

当前规则：

- `Subject: [SYNC]`
- 正文可为空
- 不要求 `In-Reply-To` / `References`
- 该动作不创建 task
- 该动作不创建 runnable thread/session
- 该动作不触发 Codex / OpenCode backend run
- 当 relay operator 显式开启 TaskMail direct ingress 时，Android 可通过 `/relay` 提交 bootstrap `sync_project_folders` packet
- `taskmail-bootstrap-control-contract-v1` 仍表示 bridge-to-mail：relay 接受后把请求桥接回 canonical `[SYNC]` mail ingress，最终结果仍是邮箱中的 `[SYNC] Project Folder List`
- `taskmail-bootstrap-control-contract-v2` 表示 direct request + direct result：当前 relay runtime 在具备本地 PC truth 时会直接返回 `packet_ack + bootstrap_result`
- 当 relay operator provision 了 shared `/control` 当前切片时，Android 也可通过 `/control` 提交同一业务动作；bootstrap client 仍必须检查 `hello_ack.accepted_payload_schemas` 里是否出现 `taskmail-bootstrap-control-contract-v2`
- 在 `/control` 上，同一业务语义当前投影为 `command(sync_project_folders) -> command_ack -> result(sync_project_folders_result)`
- `/control.hello_ack.accepted_payload_schemas` 当前按 runtime 已 provision 的 handler 动态回告；该列表当前可出现：
  - `post-creation-session-action-contract-v1`
  - `taskmail-bootstrap-control-contract-v2`
  - `taskmail-transport-probe-payload-v1`
- current repo 中，runtime 以已配置 `task_root` 作为本地 truth 可用的运行时信号；v2 handler 读取 runner config 中的 `project_sync_roots`，而不是扫描 relay/VPS 自己的临时目录
- accepted v2 path 不会额外生成 `[SYNC] Project Folder List` 回复邮件

当前返回内容：

- 回复 `D:\projects` 与 `E:\projects` 的一级子目录列表
- 不递归
- 不列文件
- 某个根路径不可用时，单独报告该根的错误状态，而不是整次失败
- v2 `bootstrap_result.sync_project_folders_result.canonical_body_text` 与 canonical `[SYNC] Project Folder List` 正文保持同一业务语义

当前边界：

- `[SYNC]` 回复邮件不携带 task 专用 `state capsule`
- `[SYNC]` 回复邮件不携带 `question capsule`
- live mailbox 中，system 生成的 `[SYNC] Project Folder List` 回复全局只保留最新一封；发送新的 `[SYNC]` 回复后，runtime 会删除更早的 `[SYNC]` 系统回复
- direct `bootstrap_result` 不是 status mail；它同样不携带 `state capsule` / `question capsule`，也不进入 thread/session projection
- accepted v2 direct path 不参与 `[SYNC]` 邮件保留与清理，因为这一路径不再产出 `[SYNC]` 回复邮件
- 用户若要真正发任务，仍需另起 `[OC]` 或 `[CX]` 邮件

### 3.2 New Task Mail

### Subject

支持的新任务主题前缀：

- `[OC]` -> OpenCode backend
- `[CX]` -> Codex backend
- `[KILL] <task_id>` -> direct kill request

### Body

首封任务邮件维持半结构化格式。

当前核心字段：

- `Repo:`
- `Task:`

可选字段：

- `Workdir:`
- `Timeout:`
- `Mode:`
- `Profile:`
- `Permission:`
- `Acceptance:`

当前规则：

- 新任务邮件仍然是创建 fresh `thread/session` 的唯一正式入口
- 可配置 `new_task_max_age_minutes` 首封时效保护；当该值大于 `0` 时，只接受 `Date` 位于当前 runner 时间窗口内的非 reply `[OC]` / `[CX]` 首封任务
- 若首封任务超出该时窗，或 `Date` 无法解析，则直接忽略，不创建 thread/session，也不触发 backend run
- 上述时效保护只作用于首封新任务，不作用于 reply continuation、`[SYNC]` 或 direct `[KILL]`
- 缺少 `Workdir` 时允许为空
- 缺少 `Timeout` 时使用默认值
- 缺少 `Mode` 时默认 `modify`
- `Permission` 当前只支持 `default` / `highest`
- 首封任务未写 `Permission` 时，使用后端默认权限

### 3.3 Optional Direct `new_task` Ingress

For the current Phase 2 v1 slice, the relay may optionally accept a direct Android `new_task` packet over `/relay`.

Current boundary:

- this ingress is limited to `phase2-direct-outbound-contract-v1`
- only action `new_task` is accepted on this direct path
- `dispatch_metadata.fallback_policy` currently accepts `mail` and `none`
- `none` is a legal direct-only client declaration and is not rejected at parser level solely because it disables legacy fallback
- the server bridges the accepted packet into the current bot-mailbox first-mail ingress
- the PC mail runner still consumes the canonical first-mail body and remains task-execution truth
- direct ingress does not change reply headers, reply routing, or the current mail-visible status contract

### Permission Field

当前 `Permission` 字段规则：

- 允许值：`default`、`highest`
- 首封任务省略 `Permission`：使用后端默认权限
- reply 省略 `Permission`：继承当前 thread/session 已持久化的权限值
- reply 显式 `Permission: default`：恢复到后端默认权限
- reply 显式 `Permission: highest`：请求当前仓库支持的最高权限执行模式

当前 backend 投影：

- `Codex`：`highest` 映射到 `--dangerously-bypass-approvals-and-sandbox`
- `OpenCode`：`highest` 映射到当前 run 目录中的临时 merged config overlay，不改写用户全局配置

当前 live 验证：

- 2026-03-16 已通过真实邮箱验证 `Codex` 和 `OpenCode` 的 `Permission: highest -> reply omit inherit -> Permission: default` 三步链路
- 验证范围包括状态邮件里的 `Permission` 展示、thread/session 持久化，以及 backend-specific 权限投影

## 4. Reply Routing

当前 reply 路由优先级固定为：

1. `In-Reply-To`
2. `References`
3. state capsule
4. 主题中的 `[S:session_id]`

当前明确不做：

- 非 reply 新邮件按标题自动复用旧 session
- 依赖“相同主题”做隐式命中

## 5. Reply Actions

当前支持的 reply 动作：

- 普通 reply：继续当前 session
- `/pause`
- `/continue <session_id>`
- `/resume`
- `/end`
- `/restart-runner`
- `/new`
- `/sessions`
- `/status`
- `/rerun`
- `/kill`

reply 正文中的结构化覆盖字段当前包括：

- `Profile:`
- `Permission:`
- `Timeout:`
- `Mode:`
- `Task:`
- `Acceptance:`

当前边界：

- `/sessions` 仍是当前 workspace 的发现入口，并会提供可复制的 targeted command 提示
- 当前支持显式 same-workspace session targeting，但不支持 cross-workspace switching
- `/pause` 只暂停邮件控制面的后续 continuation，不暂停已经在跑的底层 CLI 进程；对 `accepted/running` 线程应提示用户等待或使用 `/kill`
- `/end` 只对非运行中的 thread/session 生效；它只把当前 thread/session 的 lifecycle 改成 `ended`，不改写上一轮 `done` / `failed` / `killed` / `paused` 结果；对 `accepted/running` 线程应提示用户等待或先 `/kill`
- thread 进入 `paused` 后，普通 reply 不会隐式恢复；需要显式 `/resume`
- 若 `paused` 线程仍有 pending question set：
  - `/resume` 不带答案：退出 paused，恢复成 `[QUESTION]`
  - `/resume` 带答案：按正常 answer flow 继续解析；答不全则保持 `[QUESTION]`
- 若 `paused` 线程没有 pending question set：
  - `/resume` 恢复为普通 continuation / native resume 语义

Current targeted routing update:

- same-workspace explicit session targeting is now supported for `/status <session_id>`, `/last <session_id>`, `/continue <session_id>`, `/pause <session_id>`, `/resume <session_id>`, `/end <session_id>`, and `/kill <session_id>`
- `/sessions` remains the discovery entrypoint for the current workspace, but now also includes copyable targeted command hints
- targeted command results continue on the target session's own mail chain instead of the invoking thread
- cross-workspace switching and hidden title-based guessing remain unsupported
- `/status` is a current-state query only: if the session is `running`, it reports `Summary: Running.` and uses `Reply:` to show the latest local assistant-visible output from the current live session; if no assistant output is available yet, it replies with `Reply: No assistant output yet.`; if the session is not `running`, it explicitly says so and reports the current local thread state instead of replaying the previous run result
- `/last` is a local last-result query only: it returns the latest persisted result for the session without starting a new backend run
- `/restart-runner` is a local hosted-loop control command: it does not call the backend, it queues a local runner restart request, and the current Windows host schedules that restart through an external detached launcher so the control mail itself does not kill the host inline

## 6. Waiting-State Protocol

等待态协议规则：

- backend 必须输出显式 `question capsule`
- 单轮等待态可以包含一个或多个问题
- 单题回复保持低摩擦
- 多题回复使用结构化 `Answers:` 行
- resume 输入使用 canonical answers，而不是原始自由文本

专题协议：

- [multi_question_protocol.md](./multi_question_protocol.md)

## 7. Attachment Semantics

当前附件规则：

- incoming attachments 可 materialize 到 active `workdir`
- attachment-only reply 默认走 `CONTINUE_SESSION`
- 若线程当前在 waiting state，则 attachment-only reply 默认走 `ANSWER_QUESTION`
- outgoing files 通过 `manifest.json` 或 artifact 目录 fallback 暴露

专题协议：

- [multimedia_mail_protocol.md](./multimedia_mail_protocol.md)

## 8. Status Mail Contract

当前用户可见状态邮件标签：

- `[ACCEPTED]`
- `[RUNNING]`
- `[DONE]`
- `[FAILED]`
- `[KILLED]`
- `[PAUSED]`
- `[STATUS]`
- `[QUESTION]`
- `[SYNC]`

当前状态邮件承担：

- 用户可读状态摘要
- 当前 `Permission` 展示
- state capsule
- waiting-state 问题模板
- 附件 / inline image 回传
- paused 恢复提示

补充说明：

- `[SYNC]` 是系统控制邮件标签，但它不属于 task thread 状态流转
- `[SYNC]` 邮件不携带 task `state capsule`，也不参与 session projection
- task thread 的状态邮件现在按三类处理：
- `progress`: `[ACCEPTED]`、`[RUNNING]`、`[STATUS]` 采用 replacement 语义；发出新的 progress mail 后，runtime 只会删除该 thread 里更早的 progress mails
- `action_required`: `[QUESTION]`、`[PAUSED]` 保留在 live mailbox，供用户后续操作
- `receipt`: `[DONE]`、`[FAILED]`、`[KILLED]` 保留在 live mailbox，作为明确结果回执
- 完整的系统状态邮件历史仍保留在本地 thread archive 中

## 9. Thread / Session Relationship

当前 mail protocol 运行在 hybrid thread/session 模型之上：

- run artifacts 仍按 thread 落盘
- scheduling / queueing 以 workspace + session 为控制面
- reply continuation 可复用 native backend session id
- thread/session 现在额外持久化 `lifecycle: active|ended`；`/end` 会把当前 thread 标记为 `ended` 并退出 active working set
- `/resume` 可将 ended thread 恢复回 `active`；如果 ended thread 处于 paused/question waiting 路径，恢复后仍沿用同一条 thread/session
- `paused` 是 thread / session 的显式控制面状态；它保留 `paused_from_status` 以说明是从 `done` / `failed` / `killed` / `awaiting_user_input` 哪条主状态流转入
- `paused` 不是 active run；runner 不会把它当作 `running` 或 `accepted` 继续调度
- `[SYNC]` 不进入这套 thread/session 状态模型；它是 mail control plane 的独立只读动作

专题状态文档：

- [session_scheduler_status.md](./session_scheduler_status.md)

## 10. Current Non-Goals

当前仍明确不做：

- 第二套非邮件控制协议
- 隐式 session switch
- 按标题自动复用旧 session
- 通用附件推断式处理
- 将当前仓库直接扩成完整平台

## 11. Current Open Gaps

当前与代码实现一致的主要缺口：

- 非 reply 新邮件仍不会按 `workspace + title` 自动复用已有 session
- 显式 session targeting 目前只支持 same-workspace command routing；cross-workspace switching 和非 reply reuse 仍未实现

当前已经固定下来的真实邮箱 acceptance：

- `scripts/live_smoke_mail_question_answer.py` 覆盖真实 backend 的 `[QUESTION] -> ANSWER -> DONE`
- `scripts/live_smoke_mail_kill.py` 覆盖正常 mailbox loop 下的真实 backend `KILL`

当前已经固定下来的 structured run-result projection：

- backend 可以在最终回复里追加 `---TASK-RUN-RESULT-BEGIN---` / `---TASK-RUN-RESULT-END---` 结果块
- adapter 会把其中的 `changed_files`、`tests_passed`、`error_type`、`error_message` 投影到 `RunResult`
- 结果块不会继续出现在用户可见的 `Reply:` 正文里

这些剩余工作见：

- [mail_adapter_refactor_plan.md](../plans/mail_adapter_refactor_plan.md)
- [coding_backlog.md](../plans/coding_backlog.md)

## 12. Archived Source Docs

旧版来源文件已归档到：

- `docs/archive/multi_question_protocol_implementation_legacy.md`
- `docs/archive/multimedia_mail_design_legacy.md`
- `docs/archive/session_scheduler_plan_legacy.md`

当前对外应只使用 `docs/current/` 下的 canonical 文档。
