# Current Protocols

本目录存放**当前仓库已经在使用或正在对齐的正式协议文档**。

这些文档的职责是描述 `mail_based_task_manager` 现在如何工作，而不是描述未来完整 Task Manager 平台。

当前主文档：

- `mail_protocol.md`
- `android_runner_communication_contract.md`
- `taskmail_direct_control_file_contract.md`
- `android_reply_method_rules.md`
- `task_view_mail_parsing_rules.md`
- `multi_question_protocol.md`
- `multimedia_mail_protocol.md`
- `pc_mail_output_protocol.md`
- `session_scheduler_status.md`
- `runtime_handoff_shutdown.md`

Current outbound reference skeleton:

- `reporter_output_skeleton.py`

使用规则：

- 当前行为变化时，优先更新本目录文档
- 如与 [README.md](../../README.md) 或 [state.md](../../state.md) 冲突，以当前事实为准
- 如与 `docs/plans/` 或 `docs/platform/` 冲突，以本目录定义的当前协议边界为准
- 可复用操作手册、冒烟步骤、环境经验默认放 `docs/reference/`，除非它们已经上升为当前协议或运行时真相
- TaskMail direct relay/control/file 相关 current truth 以 `taskmail_direct_control_file_contract.md` 为中心，再配合 `mail_protocol.md`、`android_runner_communication_contract.md`、`multimedia_mail_protocol.md` 与 `pc_mail_output_protocol.md` 阅读；2026-03-24 起当前已落地 shared `/control` 的两条已实现能力：bootstrap `sync_project_folders v2`，以及 relay-side `transport_probe` harness；其中 `transport_probe_result` 现在会在可见时等待并投影 `_mailbox/transport_probes/` 里的 PC observation，区分 `observed / timed_out / submitted / failed`
- repo-side 现在还额外存在一个 operator-only debug 入口：`POST /debug/pc-control/dispatch`。它与 `/relay`、`/control`、`/v1/files` 复用同一 `Authorization: Bearer <transport_token>` admission，只负责把一条 `command_dispatch` 放进当前 live `pc_control_runtime`，不引入新的 user-facing business API；对应本地 CLI 是 `.\.venv\Scripts\python.exe .\scripts\pc_control_operator_dispatch.py --config <mail_config> ...`
- 对 `pc_control` 路径的 `execution_policy.profile`，当前显式 `default` 与省略 profile 的语义等价；adapter 不会因为 `profile=default` 而再额外要求本地 profile-model 映射

分层依据见 [document_layering_plan.md](../document_layering_plan.md)。

## SDK-First Transport

- New OpenCode and Codex threads now default to the SDK transport for real continuous sessions.
- `backend_transport` is persisted on snapshots, thread state, session state, and run results.
- Legacy persisted records that do not have `backend_transport` are interpreted as `cli` for backward compatibility.
- reply continuation、`/resume` 和 `ANSWER_QUESTION` 现在会继承当前 thread/session 已持久化的 `backend_transport`；显式切 backend 或显式 `/new` 时，再按目标 backend 默认 transport 重新解析。
- OpenCode runtime SDK turns now go through a short-lived local `opencode serve`; the adapter stops that temporary listener after the turn finishes.
- The SDK bridge runs through `scripts/codex_sdk_sidecar/dist/index.js`.
- The SDK adapter now injects default proxy env vars for the sidecar when `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, or `NO_PROXY` are unset, so `codex + sdk` does not depend on an interactive shell wrapper to reach the local proxy.
- The SDK sidecar now treats terminal `turn.completed` / `turn.failed` events as the end of the turn and closes the streamed iterator immediately, so a slow or wedged Codex CLI exit does not keep the mail-runner thread stuck in `running`.
- CLI remains available as fallback by storing `backend_transport: cli` on an existing thread or by setting `opencode_transport_default: cli` / `codex_transport_default: cli`.

## Session Lifecycle

- Thread state and session state now persist a lightweight lifecycle axis: `active` or `ended`.
- Thread state and session state now also persist `last_active_at`; legacy records fall back to `updated_at`.
- Thread state and session state now also persist `last_progress_at`; legacy records fall back to `updated_at`.
- The active working set is controlled by `max_active_sessions` (default `4`).
- Background execution now has two caps: `max_active_sessions` is the global limit, and `max_active_sessions_per_workspace` (default `2`) limits how many sessions from the same `repo_path + workdir` may run concurrently.
- When starting a new task or reactivating an ended thread would exceed that cap, the system automatically ends the least recently active non-running session instead of interrupting running work.
- `/end` is now available for non-running threads/sessions; it marks the current thread/session as `ended` without rewriting the last run status, and `/resume` can reactivate the same thread back to `active`.
- `/status` now reports current local state only: while a session is running it responds with `Summary: Running.` plus the latest assistant-visible output under `Reply:` when available, or `Reply: No assistant output yet.` otherwise; if the session is not running, it explicitly reports that the session is not running.
- `/last` is now available as a local last-result lookup; it returns the latest persisted result without starting a backend run.
- `/restart-runner` is now available as a hosted-loop local control command; it queues a local restart request and the Windows host executes the actual restart through an external detached launcher instead of killing itself inline from the mail-handling task.
- `mail_runner.observe` now reports active vs ended session counts and includes lifecycle details in `list-running`, `show-thread`, and `show-thread-live`.
- `new_task_max_age_minutes` can optionally enforce a first-mail freshness window for non-reply `[OC]` / `[CX]` ingress. When set to a positive number, only new-task mail whose `Date` falls within that window is accepted; stale or unparseable first-mail task ingress is skipped before any backend run starts. Reply continuation, `[SYNC]`, and direct `[KILL]` are unaffected.

## Health Visibility

- `mail_runner.observe` now derives a lightweight health layer for active sessions: `normal`, `stale`, `suspected_stuck`, or `orphaned`.
- `scripts/diagnose_runtime_health.py` now folds host state, recent polling cycles, and per-thread run/stream evidence into a one-shot operator report; by default it inspects active threads, and `--thread-id` can focus on specific sessions. `scripts/diagnose_runtime_health.cmd` is the Windows convenience wrapper for the same entrypoint.
- The first-round stale/stuck threshold is fixed at `300s`.
- `show-thread`, `show-thread-live`, and `list-running` now expose `Health`, `Last Progress At`, and idle-time context.
- For SDK-backed runs that persist `stream.events.jsonl`, observe uses the newest stream-event timestamp as live progress when it is newer than persisted state, reducing false stuck reports during active streaming or post-turn projection replay.
- `codex + sdk` currently persists sidecar-driven stream events during the turn; `opencode + sdk` now also persists same-layer `stream.events.jsonl`, but the current OpenCode first pass is a minimal post-turn projection from assistant parts (`turn.started -> assistant.completed -> turn.completed`), not a validated true incremental stream.

## Status Mail Retention

- Task-thread status mail no longer uses a single "keep only the latest mail" rule.
- Progress mail `[ACCEPTED]`, `[RUNNING]`, and `[STATUS]` is replaceable; only the latest progress mail remains in the live mailbox.
- Action-required mail `[QUESTION]` and `[PAUSED]` is retained so the user can continue operating on the thread from mail.
- Receipt mail `[DONE]`, `[FAILED]`, and `[KILLED]` is retained as durable result confirmation.
- `scripts/prune_stale_status_mails.py` follows the same rule set: it cleans stale progress mail and stale `[SYNC]` replies, not action-required or receipt mail.

## Runtime Hosting

- 当前受支持的长驻入口是 `.\.venv\Scripts\python.exe -m mail_runner.host --config .\mail_config.bot.local.yaml --runtime-dir .\_tmp_live_mail_runner`
- `mail_runner.host` 只负责 host 生命周期，内部仍然调用 `mail_runner.app.run_forever()`
- Bot mailbox receive is now best-effort `IDLE` aware: when the IMAP server advertises `IDLE`, the host waits on a long-lived receive connection and wakes early for new mail; `IDLE` reads are bounded so a stalled socket cannot block the host loop indefinitely, the host forces a periodic full mailbox sync plus `IDLE` rebuild every 5 minutes, and when `IDLE` is unavailable or unstable it automatically falls back to the existing polling loop.
- 每个 `runtime_dir` 只允许一个 host 实例
- host 生命周期状态会写入 `host_state.json`
- `scripts/manage_mail_runner.ps1` 现在会通过 `mail_runner.host` 启停后台轮询进程
- Windows 下可能会看到 `venv launcher -> real host` 两个 `python.exe`；`scripts/manage_mail_runner.ps1` 会记录 launcher/host 两个 pid，并优先以 `host_state.json` 里的真实 host pid 为准
- `scripts/manage_mail_runner.ps1` 的 `status` / `stop` / `restart` 会优先用 `Win32_Process` 识别同配置下残留的 legacy `mail_runner.app --loop`；如果当前 shell 无法读取 `Win32_Process`，则回退到 `host_state.json + loop.pid` 做稳定管理
- `scripts/manage_mail_runner.ps1` 在 `start / restart` 时会额外验证 host 是否稳定存活；若 `stop / start` 失败，脚本会把 `host_state`、`loop.pid` 和最近 stdout/stderr tail 一并带入错误输出
- 当前本地 Windows service 维护以 `runtime_dir/host_state.json` 为第一活性真相、`runtime_dir/loop.pid` 为辅助锚点；两者与 `status` 输出冲突时，优先信 `host_state.json` + 真实 PID，再看 `loop.stderr.log`
- `scripts/manage_mail_runner.ps1` 现在优先使用 `host_state.json + loop.pid` 做同 runtime 管理，`Win32_Process` 只作为缺少 runtime metadata 时的 best-effort 补充，避免某些受限 shell 里的 CIM/WMI 查询把 `status` / `start` / `restart` 卡住
- `scripts/manage_mail_runner.ps1 start / restart` 现在会通过一个外部 detached PowerShell launcher 托管 `mail_runner.host`，避免非交互 operator shell 在退出时把刚拉起的 host 子进程一起回收
- 对 relay-enabled 配置，`scripts/manage_mail_runner.ps1` 现在还会伴随管理一个 `sync_relay_task_root.py --repeat-seconds 2` companion；默认使用项目根目录下的 `work_bot.pem`，把本地 authoritative `task_root` 持续同步到 VPS relay 可见的 `/opt/mail_runner_relay/shared/task_root`，并在 `status` 中暴露 companion pid/log 诊断；该 companion 现在显式忽略 ambient SSH proxy / jump-host 配置，强制走直连 `ssh/scp`
- 这台机器上的 detached launcher 维护口径已经固定为隐藏 `Start-Process powershell.exe ...`；不要把 `Register-ScheduledTask` 当作 `start / restart` 主路径，因为它在 agent / 提权 shell 里可能卡住，导致 host 实际还没拉起
- 对 agent / 非交互终端，`start` / `restart` 可能出现“命令侧超时但 host 已经起来”的现象；此时不要立刻重复启动，先复核 `host_state.json`、`manage_mail_runner.ps1 status` 与最新 `loop.stderr.log`
- `scripts/manage_mail_runner.ps1 detach-restart` 现在会先安排一个外部 detached launcher，再由那个 launcher 执行真正的 `restart`；`/restart-runner` 邮件控制动作走的就是这条路径，避免任务内联 `restart` 把承载自己的 host 直接杀掉
- `scripts/safe_shutdown_mail_runner.ps1` / `scripts/safe_shutdown_mail_runner.cmd` 记录了当前“跨电脑 handoff 停服”流程：脚本强制要求显式 `ConfigPath` 与 `RuntimeDir`，先按同一份 config 解析 `task_root`，拒绝在 `thread_state.json` 仍有 `accepted/running` 时执行强制 shutdown，停服后再复核 `manage_mail_runner.ps1 status` 并检查同 task root 下的 tracked Codex sidecars
- runner restart recovery now keeps automatic status-mail callbacks for recovered `accepted` / queued work, so resumed runs still emit `[RUNNING]` and terminal status mails on the original reply chain
- 当前最小可观测入口是 `mail_runner.observe`
- `mail_runner.observe` 只读现有落盘状态，支持 `status`、`list-running`、`list-queue`、`show-thread <thread_id>`、`show-thread-live <thread_id>`、`follow-thread-live <thread_id>`
- SDK-backed turn 现在会在 `runs/<task_id>/stream.events.jsonl` 下落本地流式证据，用于 PC 侧只读会话窗与 `output_chunk` 投影；`codex + sdk` 当前是 sidecar live stream，`opencode + sdk` 当前是基于 assistant parts 的 post-turn minimal projection
- `codex + sdk` sidecar 现在会在 `runs/<task_id>/codex_sidecar_process.json` 下临时记录当前 sidecar pid；正常收尾时该文件会被清掉，异常残留时可用 `scripts/cleanup_project_codex.ps1` 或 `scripts/cleanup_project_codex.cmd` 按记录列出、停止或清理本仓库遗留的 tracked Codex sidecars
- `scripts/monitor_mail_runner.cmd` 会打开独立监控窗口；不带 thread 时仍循环展示 `status`、`list-running`、`list-queue`，聚焦 thread 时会切到 append-only live follow 视图，把 transcript 增量和 live stream 事件逐条追加到底部
- 聚焦 monitor 窗口现在由一个隐藏 controller 承接。如果聚焦窗口在 thread 仍处于 `active` 时被 `Ctrl+C`、右上角 `X`、或 terminal tab/pane 关闭，controller 会补发一个本地 close request：必要时先结束当前 run，再在不运行后把该 session 标记为 `ended`。总览窗口关闭时仍不会改动后端状态。
- `.\scripts\monitor_mail_runner.cmd -ThreadId <thread_id> -RequestKill` now queues a local PC-side kill request for the current running task on that thread; the host loop consumes that request from `runtime_dir/thread_kill_requests/` and routes it through the existing backend kill path.
- 在 Windows 上把 `spawn_monitor_windows: true` 打开后，后台轮询模式会为每个进入 `running` 的 thread 自动拉起一个聚焦 monitor 窗口。这些自动窗口通过 `scripts/monitor_mail_runner_controller.ps1` 拉起，仍然聚焦 `follow-thread-live <thread_id>`，只在对应 thread 仍为 `active` 时保留；controller 在真正打开 worker 前还会再检查一次 `thread_state.json`，避免线程刚脱离 `active` 时窗口闪退。`monitor_window_buffer_lines` 和 `monitor_window_history_limit` 仍继续用于限制滚动缓冲与启动回放。
- 当前双邮箱调试入口还包括 `scripts/fetch_bot_mails.cmd` 和 `scripts/fetch_user_mails.cmd`
- 当前这台机器的 relay-enabled 本地 host 维护口径是：配置文件优先用 `mail_config.bot.relay.local.yaml`，runtime dir 用 `.\_tmp_live_mail_runner`
- 如果 relay-enabled host 本身在跑，但 `manage_mail_runner.ps1 status` 的 `Relay task-root sync` 段显示 companion 未运行，`current-session` direct `reply` / `/status` 仍可能因为 VPS `task_root` 快照落后而报 locator rejection；先修 companion，再重开 Android 侧排查
- `.\.venv\Scripts\python.exe .\scripts\prune_stale_status_mails.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --dry-run --output .\_tmp_live_mail_runner\stale_mail_cleanup.json` 可按当前保留规则清理遗漏的旧 task status mails 与旧 `[SYNC]` 系统回复，并把扫描/删除结果落成 JSON
- 当前大文件交付支持两条 external delivery 路径：默认兼容策略下，如果配置了 COS，则超阈值 artifact 继续走 COS 外链；否则当启用了 `outbound_transport=relay` 且存在 `relay_url + relay_transport_token` 时，同一 relay host 会暴露 `/v1/files` file surface，runtime 会把超阈值 artifact 上传到该 file surface。若部署准备切向 `/v1/files` owner lane，现在也可以显式设置 `external_delivery_backend_preference=file_surface`，让 runtime 在 COS 仍保留配置时优先走 relay file surface；但如果某个 artifact 超过 live `/v1/files` 当前单文件上限，cutover 期间 runtime 仍会只对这类 oversize artifact 保留 `COS` 兼容交付。两条路径都会保留 `Artifacts` 区域条目、额外生成 `External Deliveries` 区域，并且不再把该超大文件作为 MIME 附件继续投递；COS 上传当前强制走直连 HTTPS，不继承宿主进程里的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，relay file-surface 上传则复用同一 relay host 与同一 Bearer transport token；该 token 当前也与 shared `/control` 首刀复用同一认证路径。成功 external delivery 现在还会在 `runs/<task_id>/artifacts/external_delivery_index.json` 下落 provider/url/expires_at 证据；若走 relay `/v1/files`，原有 `artifact_file_binding_index.json` 仍继续保留为 transport-facing `artifact_id -> file_id` 绑定 sidecar
- relay packet store 现在会在 accepted packet 后续失败时同时持久化 `last_error_message` 和 `last_error_code`，便于区分 `direct_temporarily_unavailable`、`workspace_identity_unresolved` 等 machine-readable failure classification
- terminal status mail 发送后，runtime 现在会在 `runs/<task_id>/canonical_summary.json` 下落一个 per-run canonical summary；当前字段集包含 `thread_id`、`task_id`、`run_status`、`ingress_type`、`ingress_message_id`、`request_id`、`packet_id`、`receipt_id`、`action_type`、`target_session_identity`、`last_summary`、`terminal_mail_message_id`、`terminal_mail_subject`、`generated_at`
- 对 current-session direct `/status` 与 plain `reply`，runtime 现在都会在本地 authoritative task root 的 `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json` 下落一份 thread-scoped session-action closeout；当前最小字段集包含 `action_type`、`target_session_identity`、`request_id`、`ingress_message_id`、`packet_id`、`receipt_id`、`last_summary`、`terminal_mail_subject`、`generated_at`
- 对 relay-side `/control transport_probe` 注入到 bot mailbox 的 deterministic probe mail，PC host 现在会在本地 authoritative task root 的 `tasks/_mailbox/transport_probes/<probe_id>.json` 下落一份 mailbox observation sidecar；在 relay 具备 task-root 可见性时，`/control transport_probe_result.payload.observation` 会直接复用这份 evidence；若超时未观测或当前看不到 task-root，则结果会回 `partial`
- current-session direct action resolver 仍优先读取 `session_state`；如果 relay 可见 task root 暂时缺少对应 session 索引或索引落后，runtime 仍可回退到 `thread_state`：优先使用请求显式提供的 `thread_id`，否则按 `workspace_id/session_id` 扫描 `thread_state` 候选并补解析 canonical `workspace_id/session_id/thread_id` 后继续处理；若 `session_state` 与 `thread_state` 都缺失，或补解析结果与请求 identity 冲突，仍会明确 reject
- `.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root <tasks>` 现在可以把同 run 的 PC canonical outcome、ingress/terminal raw mail 锚点、`thread_state.json`、`result.json`、`outbound/delivery_attempts.jsonl`，以及可选 relay `packets.json` / `delivery_attempts.jsonl` 组装成 `runs/<task_id>/taskmail_daily_closeout_bundle.json`；对 direct post-creation `reply` / `/status`，若存在 matching `session_action_closeout.json`，bundle 会优先把它作为 canonical outcome source，并把 `canonical_summary.json` 继续作为 supporting evidence；若 `canonical_summary.json` 缺失，则仍会优先回退到 `session_action_closeout.json`，再回退到 `thread_state.json + mail/raw_*.json` 补齐最小对账锚点
- closeout bundle 在消费 Android `taskmail_new_task_send_records.json` 时，会先做 Android retained evidence 的 record selection：优先 `request_id`，其次 `transport_message_id <-> ingress_message_id`，再按同 `repo_path/workdir` 且同 outcome family 的最近时间记录保守选取候选；这一步只是为 bundle 选 record，不等于提升 `same_run_bind` 的正式 bind level
- 当前 `same_run_bind` 的正式读法仍保持为：`request_id` -> `transport_message_id <-> ingress_message_id` -> `last_summary` 弱绑定；因此 `workspace/outcome/time` 只用于避免 fallback 行误吸同 workspace 的更新 direct sample，不单独构成强绑定
- 当前这份 canonical summary 的 ingress 归因以同 run 最近一封 user-side ingress mail 为准；这让 current-session direct `reply` 也能把 `request_id`、`receipt_id`、`action_type` 与 `target_session_identity` 绑定到本次 run，而不仅限于首封 `new_task` ingress。对 Phase 4/5 的 parity 对账来说，`request_id` 仍是 direct accepted 行的首选 join key，`ingress_message_id` 与 `terminal_mail_message_id` / `terminal_mail_subject` 提供更稳的 raw mail / terminal outcome 定位锚点
