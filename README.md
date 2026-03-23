# Mail-Based Task Manager

仓库级协作约定见 [AGENTS.md](AGENTS.md)。

本项目当前代码已经在 `Phase 8` 基础上继续落地 run artifact 交付、Markdown-first 状态渲染、backend permission control、首封 `[SYNC]` 项目目录同步入口、最小 host 宿主层、最小可观测 CLI、可选的 per-thread 自动监控窗口，以及超大产物的 COS 外链交付：运行产物仍然按 `thread` 目录落盘，但已经补上 `workspace + session` 索引、后台队列调度、native backend session resume、same-workspace explicit session targeting、`/pause -> /resume -> /end` 控制面、显式问题协议邮件闭环、`Permission:` 字段的跨 reply 持久化与后端映射、`mail_runner.host + host_state.json + runtime single-instance lock`、PC 侧本地 thread kill request，以及只读的 `mail_runner.observe status/list-running/list-queue/show-thread/show-thread-live`。对于 `codex + sdk` 路径，运行中的 turn 还会落盘 `stream.events.jsonl`，供 PC 侧会话窗显示带时间戳的 transcript + live output，并且 sidecar 现在会在看到终止 `turn.completed` / `turn.failed` 事件后立刻收尾，不再被迟迟不退出的 CLI 子进程拖住。Bot mailbox receive now supports best-effort IMAP `IDLE` on servers that advertise it, with bounded `IDLE` reads plus a forced full mailbox sync and `IDLE` rebuild every 5 minutes so a stuck long-lived socket cannot silently freeze the host loop; unsupported or unstable servers automatically fall back to the existing UID-based polling path. COS external delivery now uses a direct HTTPS client session and does not inherit ambient proxy env vars. `summary.md` 第一行、`thread_state.last_summary` 和状态邮件里的 `Summary:` 会优先展示真实后端输出的用户摘要，而不是固定模板文案。当前工作区在 `2026-03-20` 本地执行 `.\.venv\Scripts\python.exe -m pytest` 的结果为 `272 passed`。

## 当前能力

- 提供核心 dataclass、基础配置加载和启动自检入口
- 提供 `workspace.py`、`thread_store.py`、`state_capsule.py` 的本地状态与落盘能力
- 提供 `MockAdapter`、`Dispatcher` 和本地 demo `runner`
- 提供真实 `OpenCodeAdapter` / `CodexAdapter` CLI 薄封装，支持 `prompt.txt` 落盘、stdout/stderr 捕获、退出码回收和子进程 kill
- 提供真实后端输出摘要提取：成功时从 `stdout` 提取用户摘要，失败时从 `stderr` 提取主要错误，并同步到 `summary.md`、`result.json`、`thread_state.json` 和状态邮件
- 提供显式问题协议：后端可输出一个或多个 `question capsule`，系统会将该轮结果落为 `awaiting_user_input` 并发送 `[QUESTION]`
- 提供等待态回复恢复：用户回复答案后会生成新的 snapshot 并继续执行，而不是直接丢弃等待中的任务主线
- 提供失败态恢复重跑：如果 thread 已经 `[FAILED]` 且没有可恢复的 native session，普通 reply 或 `/resume` 会基于最近 snapshot 自动启动 fresh recovery run，而不是只回一封 `[STATUS]`
- 提供 `mail_io.py` 的 IMAP/SMTP SSL 接入
- 提供 `parser.py` 的 `[OC]` / `[CX]` 首封任务主题、`[SYNC]` 首封只读控制主题和任务正文解析
- 提供 `quote_extractor.py`、`context_layer.py`、`intent_parser.py`、`task_compiler.py` 的 reply 对话处理链
- 提供 `reporter.py` 的 `[ACCEPTED]` / `[RUNNING]` / `[DONE]` / `[FAILED]` / `[STATUS]` / `[KILLED]` / `[QUESTION]` 状态邮件正文生成
- task thread 的 live mailbox 现在按三类保留：`[ACCEPTED]` / `[RUNNING]` / `[STATUS]` 只保留最新进度邮件；`[QUESTION]` / `[PAUSED]` 和 `[DONE]` / `[FAILED]` / `[KILLED]` 作为 action-required / receipt 邮件保留；完整历史仍保留在 `tasks/<thread_id>/mail/raw_*.json`
- 真实 CLI / SDK 回复现在支持 structured run-result capsule：adapter 会回填 `RunResult.changed_files`、`tests_passed`、`error_type`、`error_message`，并把结果块从用户可见回复与状态邮件正文里剥离
- 提供超大产物 COS 外链交付：小文件继续作为邮件附件，超阈值文件改为预签名下载链接并展示在单独的 `External Deliveries` 区域；`APK/IPA` 会自动改用 `.bin` 对象名绕过 COS 默认域名分发限制；COS 上传当前强制走直连 HTTPS，不继承宿主进程里的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- 提供首封 `[SYNC]` 项目目录同步入口：直接回复 `D:\projects` / `E:\projects` 或配置根路径下的一级文件夹清单，不触发 backend run，也不创建 task/session
- 提供 `[PAUSED]` 状态邮件和 `/pause`、`/resume`、`/end` 的显式控制面；paused 只阻止后续 continuation，不会暂停已经在跑的 CLI 进程
- 提供 `app.py --once` 的同步批处理路径，以及 `app.py --loop` 的后台轮询路径
- 支持 reply 邮件通过 `In-Reply-To` / `References` / `state capsule` / `subject [S:session_id] tag` 命中原线程
- 支持 slash 命令版 reply 动作解析：`/pause`、`/resume`、`/end`、`/restart-runner`、`/new`、`/sessions`、`/status`、`/last`、`/continue`、`/rerun`、`/kill`
- 支持 same-workspace explicit session targeting：`/status <session_id>`、`/last <session_id>`、`/continue <session_id>`、`/pause <session_id>`、`/resume <session_id>`、`/end <session_id>`、`/kill <session_id>` 会作用到当前 workspace 的目标 session，并把结果挂回目标 session 自己的邮件链
- 支持 `profile` 字段在 snapshot / thread state / action 中持久化，并在真实 adapter 中按后端映射到实际 model 参数
- 支持 `permission` 字段在 snapshot / thread state / session state / action 中持久化；缺省 reply 会继承当前权限，`highest` 会映射到 `Codex` 的危险直通模式和 `OpenCode` 的 run-scoped overlay
- 支持在 thread/session/run 结果中持久化 `backend_session_id`，并在后续 `/resume` 时走 native `codex exec resume` / `opencode run --session`
- 支持 `tasks/_scheduler/workspaces/...` 下的 `workspace_state.json` / `session_state.json` 索引，用于 session 列表、same-workspace 并发上限控制和排队恢复
- 支持后台队列调度：同一个 `workspace(repo_path + workdir)` 内最多并行 `2` 个 session，不同 workspace 在统一的 `max_active_sessions` 上限内并发
- 支持同一 session 运行中暂存 follow-up snapshot，当前 run 结束后继续；runner 重启时可恢复 `accepted` / queued 状态，并把遗留 `running` 标记为失败
- Windows 下在 `opencode_command` / `codex_command` 为空时，会优先自动发现 `opencode.cmd` / `codex.cmd`
- 支持将 `opencode_command: demo` / `codex_command: demo` 作为本地演示后端，用于不消耗真实模型额度的验证
- 支持直接主题 `[KILL] <task_id>` 和 reply `/kill` 两种终止入口
- 已完成真实邮箱验证：Phase 2 验证了新任务 happy path；Phase 3 验证了 `NEW_TASK -> STATUS_QUERY -> KILL`；Phase 5 验证了真实邮箱入口下的 `[OC]`、`[CX]`、`STATUS_QUERY` 和 `RERUN`，并额外完成了一轮新的 live `[OC]` + `[CX]` 联调；Phase 7A 已把真实 backend 的 `[QUESTION] -> ANSWER -> DONE` 与正常 mailbox loop 下的 `KILL` 固定为可重复执行的 live acceptance
- 已完成 `Permission:` 的真实邮箱冒烟：`Codex` 与 `OpenCode` 都验证了 `highest -> inherit -> default` 三步链路，且分别命中了危险参数投影与 run-scoped overlay 投影
- 已完成 Phase 6 本地问答恢复验证：`QUESTION -> ANSWER -> DONE` 和等待态 `RERUN` 拒绝路径均已由自动化测试覆盖
- 提供根目录 `state.md` 记录每个阶段完成后的当前项目状态

## Codex Transport

- New Codex threads now default to the SDK transport for continuous sessions; old records without `backend_transport` stay on CLI for compatibility.
- The SDK bridge is implemented as a thin Node sidecar at `scripts/codex_sdk_sidecar/dist/index.js`, while CLI remains the fallback path.

## Session Lifecycle

- Thread/session persistence now carries `lifecycle: active|ended` plus `last_active_at` and `last_progress_at`.
- The active working set is controlled by `max_active_sessions` (default `4`); background execution now also uses `max_active_sessions_per_workspace` (default `2`) to cap same-workspace concurrency. If a new task or ended-thread reactivation would exceed the active working-set cap, the oldest non-running active session is auto-ended.
- Replying with `/end` on a non-running thread marks the same thread/session as `ended` without rewriting its last run status; `/resume` can reactivate that same thread back into the active working set.
- `mail_runner.observe` now surfaces active vs ended counts and shows lifecycle details in thread/session views.

## Health Signals

- `mail_runner.observe` now derives `health=normal|stale|suspected_stuck|orphaned` for active sessions with a fixed `300s` threshold.
- `show-thread` and `show-thread-live` now surface `Last Progress At`, `Health`, and idle-time context.
- On the `codex + sdk` path, recent `stream.events.jsonl` activity counts as progress, so active streaming turns do not get marked stuck just because `thread_state.json` has not been rewritten yet.

## 目录

```text
docs/
  current/
  plans/
  platform/
  research/
mail_runner/
  adapters/
  templates/
tasks/
  _scheduler/
    workspaces/
  thread_001/
tests/
config.example.yaml
README.md
requirements.txt
state.md
task.md
```

文档分层基线见 [docs/document_layering_plan.md](docs/document_layering_plan.md)。
当前活跃协议文档集中在 [docs/current/README.md](docs/current/README.md)；
当前仓库改造计划集中在 [docs/plans/README.md](docs/plans/README.md)；
未来平台文档集中在 [docs/platform/README.md](docs/platform/README.md)；
当前唯一的下一阶段开发计划在 [docs/plans/coding_backlog.md](docs/plans/coding_backlog.md)。

当前发出内容的 consumer-facing authority 以 [docs/current/pc_mail_output_protocol.md](docs/current/pc_mail_output_protocol.md) 和 [docs/current/multimedia_mail_protocol.md](docs/current/multimedia_mail_protocol.md) 为准。与 Android/Thunderbird 协同开发相关的下一步顺序已经收口为：先按 [docs/plans/android_consumer_contract_alignment_plan.md](docs/plans/android_consumer_contract_alignment_plan.md) 与 [docs/plans/android_consumer_protocol_freeze_note.md](docs/plans/android_consumer_protocol_freeze_note.md) 冻结 consumer contract，再落地 [docs/plans/p9_html_mail_projection_plan.md](docs/plans/p9_html_mail_projection_plan.md) 的窄范围 HTML projection，最后才进入 [docs/plans/outbound_mail_contract_convergence_plan.md](docs/plans/outbound_mail_contract_convergence_plan.md) 的 broader convergence。

## 当前架构现状

当前实现已经不是“纯 thread + 全局单槽”的模型，而是 `thread` 落盘与 `workspace/session` 调度索引并存的混合架构。

- 真实运行产物仍落在 `tasks/thread_xxx/`，这里仍然是日志、snapshot、result、raw mail 的事实来源
- 额外索引会落在 `tasks/_scheduler/workspaces/<workspace_id>/`，保存 `workspace_state.json` 和 `sessions/*.json`
- `workspace` 由 `repo_path + workdir` 唯一标识
- `session` 目前仍与一个邮件线程一一对应，默认 `session_id == thread_id`
- 非 reply 新邮件当前仍然总是创建新的 `thread/session`，即使 `repo_path`、`workdir` 和标题相同也不会自动复用旧 session
- reply 邮件只能命中已有 session，解析优先级是 `In-Reply-To` / `References` -> `state capsule` -> 主题里的 `[S:session_id]`
- 普通 reply、`/resume` 和回答 `[QUESTION]` 都会优先续接已有 native backend context；显式 `/new` 会从当前对话里开启 fresh session
- 如果 thread 已经 `[FAILED]` 且缺少可恢复的 `backend_session_id`，继续回复会自动退化为“基于最近 snapshot 的 fresh recovery run”，同时在 `[ACCEPTED]` 邮件里说明这是恢复重跑
- 后台调度遵循“同一 workspace 最多同时跑 `2` 个 session；不同 workspace 在统一的 `max_active_sessions` 上限内可并发”
- 如果同一 session 运行中又收到 follow-up，runner 会暂存 `queued_task_id` / `queued_snapshot_file`，当前 run 结束后继续

剩余的调度差距和后续扩展项记录在 [docs/current/session_scheduler_status.md](docs/current/session_scheduler_status.md)。

## 配置

默认会从以下位置加载配置：

1. `--config <path>`
2. 环境变量 `MAIL_RUNNER_CONFIG`
3. 仓库根目录 `config.yaml`
4. 若文件不存在，则使用内置默认值

字段名与需求文档一致，环境变量覆盖规则为 `MAIL_RUNNER_<FIELD_NAME>`，例如 `MAIL_RUNNER_POLL_SECONDS=60`。

可复制 `config.example.yaml` 为 `config.yaml` 后再按本地环境修改。

关于后端命令字段：

- `opencode_command: ""` / `codex_command: ""`
  表示使用系统默认命令发现。Windows 下优先找 `.cmd` shim。
- `opencode_command: demo` / `codex_command: demo`
  表示启用本地演示子进程，不调用真实模型服务。
- 非空普通字符串
  会被当作完整命令前缀解析，再由 adapter 追加固定子命令参数。
- `opencode_profile_models` / `codex_profile_models`
  表示后端自己的 `profile -> model` 映射。邮件和 snapshot 里只允许出现 `fast` / `strong` / `vision` 这类 profile 标签，不直接出现 raw model id。
- `max_active_sessions: 4`
  表示 active working set 上限，同时也是全局最多允许多少个 session 同时运行。
- `max_active_sessions_per_workspace: 2`
  表示同一个 `workspace(repo + workdir)` 最多允许多少个 session 同时运行；超过这个上限时，同 workspace 的后续 session 会进入队列等待。
- `auto_create_workdir: false`
  表示默认要求 `Repo + Workdir` 已存在；若设为 `true`，当 `Repo` 已存在且 `Workdir` 是仓库内的相对路径时，会在执行前自动创建该目录。
- `enable_web_search: false`
  表示是否给真实后端打开联网搜索能力。`Codex` 会附加 `--search`，`OpenCode` 会注入 `OPENCODE_ENABLE_EXA=1`。
- `spawn_monitor_windows: false`
  Windows-only。设为 `true` 后，后台轮询模式会为每个进入 `running` 的 thread 自动拉起一个聚焦监控窗口；窗口复用 `scripts\monitor_mail_runner.ps1`，只要该 thread 仍处于 `active` 就持续保留，脱离 `active` 后自动关闭。自动拉起前 controller 还会再检查一次 thread 是否仍为 `active`，避免线程刚结束时窗口一闪而退。
- `monitor_window_refresh_seconds: 5`
  表示上述聚焦监控窗口的轮询周期（秒）；窗口会按这个周期增量抓取新 transcript turn 和新的 live stream 事件，而不是整屏重绘。
- `monitor_window_buffer_lines: 1000`
  表示 Windows 聚焦监控窗口的控制台 scrollback 上限；脚本会尽量把窗口缓冲区限制在最近这些行，避免窗口内容无限增长。
- `monitor_window_history_limit: 12`
  表示聚焦监控窗口启动时最多回放多少个已归档 transcript turn；后续新增内容仍会继续增量追加。
- `project_sync_roots`
  表示首封 `[SYNC]` 项目目录同步动作允许扫描的根路径列表。默认值是 `D:\projects` 和 `E:\projects`；回复只会列出这些根下的一级文件夹，不递归，不列文件。
- `cos_region` / `cos_bucket` / `cos_secret_id` / `cos_secret_key`
  表示 COS 外部交付所需的地域、桶和访问凭证；也可放在本地专用的 `mail_config.cos.local.yaml` 中，仅用于超大产物外链交付。
- `external_delivery_threshold_mb: 20`
  表示从多大开始不再把产物作为邮件附件发送，而是改走 COS 外链。
- `cos_presign_expire_seconds: 604800`
  表示 COS 下载预签名链接的有效期，默认 7 天。
- `cos_object_prefix: mail-runner`
  表示 COS 对象键前缀；默认对象路径是 `mail-runner/<thread_id>/<task_id>/<filename>`。
- `Permission:` 是邮件 / snapshot 字段，不是配置文件字段
  当前只支持 `default` / `highest`。首封任务省略时走 backend 默认权限；reply 省略时继承当前 thread/session；`highest` 会投影为 backend-specific 的最高权限运行模式。

本地文件约定：

- 仓库内 `config.example.yaml` 是可提交的示例配置。
- 本地真实配置建议放在 `config.yaml`、`mail_config.local.yaml`、`mail_config.bot.local.yaml` 或 `mail_config.cos.local.yaml`，这些都应视为本地敏感文件，不提交。
- 推荐双邮箱部署：`mail_config.bot.local.yaml` / `._tmp_live_mail_runner\mail_config.loop_30s.yaml` 供 runner 使用；`mail_config.local.yaml` 可继续保留为用户邮箱或发件端本地配置。
- `tasks/` 是运行态状态目录，仓库只保留 `tasks/.gitkeep` 作为空目录占位。
- `_tmp_*` 目录用于本地验证和联调产物，可按需保留排障，也可在验证完成后自行清理。
- 根目录 `.gitignore` 已包含 `.venv/`、`.pytest_cache/`、`config.yaml`、`mail_config.local.yaml`、`mail_config.*.local.yaml`、`mail_config.cos.local.yaml`、`tasks/*` 和 `_tmp_*/`。

## 运行

推荐优先使用仓库内虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m mail_runner.app
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m mail_runner.runner --snapshot .\seed.json
.\.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.bot.local.yaml
.\\.venv\\Scripts\\python.exe -m mail_runner.app --loop --config .\\mail_config.bot.local.yaml
.\\.venv\\Scripts\\python.exe -m mail_runner.host --config .\\mail_config.bot.local.yaml --runtime-dir .\\_tmp_live_mail_runner
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml status
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml list-running
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml list-queue
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml show-thread thread_048
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml show-thread-live thread_048
.\\.venv\\Scripts\\python.exe -m mail_runner.observe --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml follow-thread-live thread_048 --exit-when-inactive
.\\.venv\\Scripts\\python.exe -m mail_runner.runtime_control request-thread-kill thread_048 --runtime-dir .\\_tmp_live_mail_runner --config .\\_tmp_live_mail_runner\\mail_config.loop_30s.yaml
.\\.venv\\Scripts\\python.exe -m mail_runner.runner --snapshot .\\seed.json --config .\\config.yaml
.\\.venv\\Scripts\\python.exe .\\scripts\\live_smoke_cos_roundtrip.py --cos-config .\\mail_config.cos.local.yaml --source .\\README.md
```

后台服务推荐直接使用仓库内脚本：

```powershell
.\scripts\start_mail_runner.cmd
.\scripts\restart_mail_runner.cmd
.\scripts\monitor_mail_runner.cmd
.\scripts\fetch_bot_mails.cmd
.\scripts\fetch_user_mails.cmd
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 stop
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\safe_shutdown_mail_runner.ps1 -ConfigPath .\_tmp_live_mail_runner\mail_config.loop_30s.yaml -RuntimeDir .\_tmp_live_mail_runner
```

脚本默认行为：

- 默认优先使用 `._tmp_live_mail_runner\mail_config.loop_30s.yaml`；如果不存在，则回退到仓库根目录 `mail_config.bot.local.yaml`，最后才回退到 `mail_config.local.yaml`
- `scripts\manage_mail_runner.ps1` 现在会启动 `mail_runner.host`，由它包装 `run_forever()` 并在 runtime dir 下写入 `host_state.json`
- `scripts\manage_mail_runner.ps1` 的 `status / stop / restart` 会优先通过 `Win32_Process` 识别当前 `mail_runner.host` 和同配置下残留的 legacy `mail_runner.app --loop`；如果当前 shell 无法读取 `Win32_Process`，则会回退到 `host_state.json + loop.pid` 做稳定管理
- `scripts\manage_mail_runner.ps1` 在 `start / restart` 时会额外等待 host 进入稳定存活窗口，不再只因为 `host_state.json` 刚写出就判定启动成功；若 `stop / start` 失败，错误里会附带 `host_state`、`loop.pid` 和最近日志 tail 便于排障
- `scripts\manage_mail_runner.ps1 detach-restart` 现在会先安排一个外部 detached launcher，再由该 launcher 执行真正的 `restart`；邮件里的 `/restart-runner` 会走这条受控路径，避免任务内联 `restart` 直接把承载自己的 host 杀掉
- `scripts\safe_shutdown_mail_runner.ps1` 会把“跨电脑 handoff 停服”固化成一个安全流程：要求显式 `ConfigPath` / `RuntimeDir`，按同一份 config 解析 `task_root`，拒绝在 `thread_state.json` 仍有 `accepted/running` 时执行强制 shutdown，停服后再复核 `status` 并检查同 task root 下的 tracked Codex sidecars
- 运行态 pid、stdout/stderr 和辅助脚本都落在 `._tmp_live_mail_runner\`
- `loop.pid` 现在会记录 launcher/host 两个 pid，便于在受限 shell 下稳定 `stop / restart`
- `status` 会先显示 `host_state.json`，再显示当前进程信息；拿不到完整命令行时会退回到 pid 文件 / host state 的有限视图
- `mail_runner.observe` 是最小可观测 CLI，只读现有状态文件，不引入新的持久化层；当前支持 `show-thread-live` 做静态快照，也支持 `follow-thread-live` 以 append-only 方式持续输出 thread transcript 增量和当前 `sdk` live stream
- `monitor_mail_runner.cmd` 会弹出独立监控窗口；不带线程号时仍循环显示 `status / running / queue`，聚焦单个 thread 时例如 `.\scripts\monitor_mail_runner.cmd -ThreadId thread_048` 会切到 append-only live follow 视图，不再整屏刷新
- 在当前低配版里，`Ctrl+C`、直接点窗口右上角 `X`、或关闭 terminal tab/pane，都只会结束监控窗口本身，不会自动结束对应的 running thread
- 如需从 PC 侧请求结束当前 running thread，必须显式执行 `.\scripts\monitor_mail_runner.cmd -ThreadId thread_048 -RequestKill`
- 当 `spawn_monitor_windows: true` 时，后台轮询模式还会在 Windows 上为每个进入 `running` 的 thread 自动拉起一个聚焦监控窗口；这些自动窗口会聚焦 `follow-thread-live <thread_id>`，并应用 `monitor_window_buffer_lines` 与 `monitor_window_history_limit`，只在对应 thread 仍为 `active` 时保留，脱离 `active` 后自行退出
- `fetch_bot_mails.cmd` 会默认抓取 bot mailbox，并把结果写到 `._tmp_live_mail_runner\recent_bot_100_mails.json`
- `fetch_user_mails.cmd` 会默认抓取 user mailbox，并把结果写到 `._tmp_live_mail_runner\recent_user_100_mails.json`
- `restart` 会先停止旧的 mail-runner 进程链，再用最新代码重启后台轮询
- `status` 会列出当前 mail-runner 的 `cmd -> powershell -> python` 进程链，方便确认服务是否真的在跑
- 推荐让 runner 只登录 bot mailbox；用户和安卓端只登录自己的 mailbox，始终把 bot mailbox 当收件人使用

真实邮箱冒烟脚本：

- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_roundtrip.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend codex`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_roundtrip.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend opencode`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_permission.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend codex`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_permission.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend opencode`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_question_answer.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend opencode`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_kill.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml --backend codex`
- `.\.venv\Scripts\python.exe .\scripts\live_smoke_mail_sync.py --config .\_tmp_live_mail_runner\mail_config.loop_30s.yaml --sender-config .\mail_config.local.yaml`

当前最新 `Permission:` live 结果落在：

- `._tmp_live_mail_permission_smoke\codex-permission-20260316_005939-b22854\result.json`
- `._tmp_live_mail_permission_smoke\opencode-permission-20260316_011028-77bf33\result.json`

当前最新 `[SYNC]` live 结果落在：

- `._tmp_live_mail_sync_smoke\sync-20260316_230059-cdf157\result.json`

本地 demo `snapshot` 示例：

```json
{
 "backend": "opencode",
  "profile": null,
  "permission": null,
  "repo_path": "D:\\repo",
  "workdir": "src",
  "task_text": "Refactor the module without changing the API.",
  "acceptance": [
    "pytest passes",
    "brief summary"
  ],
  "timeout_minutes": 30,
  "mode": "modify"
}
```

真实首封任务邮件示例：

```text
Subject: [SYNC]
```

```text
Subject: [OC] Refactor floor_shear

Repo: D:\proj\my_repo
Workdir: src\postprocess
Timeout: 60
Mode: modify
Profile: strong
Permission: highest

Task:
把 floor_shear.py 重构为 dataclass 风格，保持现有输出不变

Acceptance:
1. pytest tests/test_floor_shear.py 通过
2. 不改 public API
3. 输出简短修改说明
```

```text
Subject: [CX] Analyze floor_shear

Repo: D:\proj\my_repo
Mode: analysis_only

Task:
先梳理 floor_shear.py 的调用链和潜在风险，不改代码
```

回复邮件示例：

```text
/status
```

```text
/pause
```

```text
/resume
Permission: highest
请继续把日志目录也整理一下。
```

```text
/new
Timeout: 120
Mode: analysis_only
Task:
Only analyze the issue and list risks.
```

```text
/sessions
```

普通 reply 继续当前 session 的示例：

```text
请继续把日志目录也整理一下，并说明为什么这样改。
```

主题兼容说明：

- 首封项目目录同步主题使用 `[SYNC]`
- 新任务主题仍然使用 `[OC]` / `[CX]`
- reply 主题除了常见的 `Re:` / `FW:` / `Fwd:`，现在也兼容中文客户端常见的 `回复:` / `回复：` / `答复:` / `答复：`
- reply 命中线程时优先看 `In-Reply-To` / `References`；主题前缀兼容主要用于更稳定地做 `subject_norm` 归一化，而不是用“相同主题”强行串 session

等待提问后的回复示例：

```text
Yes, update both modules.
```

```text
Profile: fast
Permission: highest
Use the lighter profile first, then summarize the risks.
```

## 问题与回答规则

### 后端如何提问

- 后端如果需要用户补充信息，必须在 stdout 中输出显式 `question capsule`，系统才会把这一轮识别为 `[QUESTION]`
- 一次等待态里可以输出多个 `question capsule`；这时它们必须共享同一个 `question_set_id`
- 每个 `question capsule` 至少应包含 `question_set_id`、`question_id`、`question_type`、`required`、`question_text`
- 选择题还应包含 `choices`；如需展示中文选项名，应同时提供 `choice_labels`
- 自由文本描述“我有个问题”不会被识别成等待态；必须用显式协议块

示例：

```text
---TASK-QUESTION-BEGIN---
question_set_id: phase2_clarifications
question_id: phase2_entry_position
question_type: single_choice
required: true
question_text: Where should the Tasks drawer entry be placed?
choices: top | below | section
choice_labels: top=账户列表上方 | below=账户列表下方（设置附近） | section=独立分区
---TASK-QUESTION-END---
```

### 单题如何回答

- 如果当前只存在 1 个待回答问题，仍然支持直接自然语言回复
- 也支持 `question_id: value` 形式
- 也支持 `/resume` 加正文
- 如需切换档位或权限，可以在正文前加 `Profile: fast` / `Profile: strong` 和 `Permission: default` / `Permission: highest`

示例：

```text
Yes, update both modules.
```

```text
/resume
Profile: strong
Permission: highest
Proceed with both modules and keep the public API unchanged.
```

### 多题如何回答

- 如果当前存在 2 个及以上待回答问题，推荐使用结构化回复
- 推荐格式是逐行写 `question_id: value`
- `value` 优先写 canonical key；如果直接写状态邮件中展示的选项文案，系统也会尝试归一化到 canonical key
- `Answers:` 标题是可选的，主要用于便于复制模板
- `:` 和 `：` 都接受
- 解析器只会接受当前这封 `[QUESTION]` 里仍然 pending 的 `question_id`

推荐格式：

```text
Answers:
phase2_entry_position: below
phase2_icon_strings: provide
phase2_k9_support: thunderbird_only
phase2_device_validation: acceptable
```

也支持更贴近真实邮箱回信的写法：

```text
question_id: phase2_entry_position
账户列表下方（设置附近）
question_id: phase2_icon_strings
你提供
question_id: phase2_k9_support
仅 Thunderbird
question_id: phase2_device_validation
可接受
```

### 多题回复里什么算有效

- `question_id` 必须是当前仍待回答的问题 id
- 选择题答案必须能归一化到允许的某个 canonical key
- 同一个 `question_id` 如果出现多次，以最后一个有效值为准
- 空行会被忽略
- 回复中的旧引用、旧 state capsule 和旧 question capsule 不会算成新答案

### 多题回复里什么不支持

- 只写一段散文，不带任何可识别的 `question_id: value` 结构
- 回答未知 `question_id`
- 选择题填了不在允许集合里的值
- 指望系统从自由文本里猜多题答案含义

### 部分回答与继续执行

- 多题场景允许部分回答
- 如果只答对了其中一部分，系统会先保存已经校验通过的答案，继续保持 `awaiting_user_input`
- 下一封 `[QUESTION]` 会列出 `Received Answers` 和剩余未回答题目，避免用户重复填写
- 只有必填题全部补齐后，系统才会继续 resume 后端
- resume 给后端的是规范化后的答案摘要，不是原始回信全文

## Paused 协议

- `/pause` 只对非运行中的 thread/session 生效；如果当前是 `accepted` 或 `running`，系统会提示等待完成或使用 `/kill`
- paused 后，thread 和 session 都会进入 `paused`；同时保留 `paused_from_status`，用于说明它是从 `done`、`failed`、`killed` 还是 `awaiting_user_input` 暂停下来的
- paused 后，普通 reply 不会偷偷继续执行；需要显式 `/resume`
- 如果 paused 线程仍然有 pending question：
  - `/resume` 不带答案：退出 paused，重新发 `[QUESTION]`
  - `/resume` 带答案：按正常 answer flow 继续；答不全则继续停在 `[QUESTION]`
- 如果 paused 线程没有 pending question，`/resume` 会回到普通 continuation / native resume 路径

## Session 生命周期

- `lifecycle` 与上一轮运行结果分离；`done`、`failed`、`killed`、`paused` 都不会自动等于 `ended`
- `/end` 只对非运行中的 thread/session 生效；它会把当前 thread/session 标成 `ended` 并退出 active working set，但不会改写上一轮结果
- 如果当前 thread 仍是 `accepted` 或 `running`，`/end` 会拒绝并提示等待完成或先 `/kill`
- `ended` thread 之后仍可用 `/resume` 恢复到同一条 thread；如果它只是 paused 且还在等问题答案，`/resume` 也会把同一 thread 拉回 `active`

## 当前限制

- 当前仍然只支持单用户、本地、文件系统落盘，不提供数据库、Web UI、多租户或分布式 worker
- `--once` 仍然是同步批处理；只有 `--loop` / 后台 runner 路径才会利用队列和 `max_active_sessions`
- 非 reply 新邮件当前总是创建 fresh session；“按 workspace + 标题自动复用已有 session” 还没有落地
- `/sessions` 现在仍是当前 workspace 的发现入口，但会附带可复制的 targeted command 提示；同 workspace 下可以直接用 `/status <session_id>`、`/last <session_id>`、`/continue <session_id>` 等显式命中目标 session
- reply 线程恢复现在只接受显式 session 线索：优先 header，再用 state capsule，最后才用 subject 里的 `[S:session_id]` 标签；不再使用“同主题自动接旧 session”的兜底策略
- 普通 reply 默认会续接当前线程对应的 native backend context；只有新发邮件或显式 `/new` 才会启动 fresh session。`[QUESTION]` 等待态下，单题仍允许直接回答；多题应使用结构化回复，不强制写 `/resume`
- `FAILED` 线程现在分成两种恢复路径：有可恢复 native session 时继续走原生 resume；没有 native session 时，把这封回复重放成一轮新的 recovery run，并保留回复中的补充上下文
- 邮件正文提取现在支持 `text/plain` 优先、空 plain part 自动回退 `text/html`；对只发 HTML 正文的网页邮箱更友好
- 当前的问答恢复仍然是“应用层生成新 snapshot + native resume”，不是同一个 CLI 进程原地继续执行
- `profile` 现在会在真实 adapter 中映射为后端自己的 `-m` 参数；如果 profile 已设置但对应映射缺失，本轮会直接失败并返回清晰错误
- 推荐双邮箱：runner 只登录 bot mailbox，用户只在自己的 mailbox 里读信和回复。当前收件端已改为 `INBOX` 的 IMAP UID 增量扫描 + 本地 `UID/Message-ID` 去重，不再依赖 `UNSEEN` 作为唯一消费条件；当 bot mailbox server 支持 `IDLE` 时，host 会优先走 best-effort `IDLE` 唤醒，新信到达后尽快拉取，否则自动退回到原有 polling。
- 真实 CLI / SDK 的 structured run-result capsule 现在会回填 `changed_files`、`tests_passed`、`error_type` 和 `error_message`；没有结果块时仍回退到现有启发式 summary / error 提取
- 真实后端摘要提取仍是启发式规则，不保证对所有未来 CLI 输出格式都完美
- 已将 `[QUESTION] -> ANSWER -> DONE` 和真实 backend `KILL` 纳入固定的“真实邮箱入口 + 真实后端”验收项；最新工件见 `._tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74\result.json` 与 `._tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5\result.json`
- 问题识别只支持显式 `question capsule` 协议，不支持从任意自由文本里猜测“这是一条问题”；多题回答也不支持从自由文本散文里猜答案
- `[SYNC]` 目前只是“先拿项目目录清单”的只读入口；它不会自动把目录选择回填成后续 `[OC]` / `[CX]` 首封任务模板

## 验证现状

- `2026-03-18` 本地自动化验证：`.\.venv\Scripts\python.exe -m pytest` -> `247 passed`
- 自动化测试已经覆盖新任务 happy path、首封 `[SYNC]` 目录同步、reply resume、失败后 fresh recovery run、`/new` fresh session、`/sessions` 列表、same-workspace targeted `/status` / `/continue` / `/resume`、`/pause -> /resume -> /end`、ended thread reactivation、`QUESTION -> ANSWER -> DONE`、线程健康判定、same-workspace 并发上限与同线程 follow-up 排队、跨 workspace 并发、runner 重启恢复，以及 CLI resume 命令构造
- 真实邮箱 / 真实 backend 的联调记录仍保留在 `state.md`，其中 `[OC]`、`[CX]`、`STATUS_QUERY`、`RERUN`、真实 `[QUESTION] -> ANSWER -> DONE` 和正常 mailbox loop 下的真实 backend `KILL` 都已有落盘工件

## Troubleshooting

- `python` 指向 Windows Store 占位程序时，请直接使用仓库内 `.venv\Scripts\python.exe`。
- Windows PowerShell 可能因执行策略阻止 `opencode.ps1` / `codex.ps1`；当前实现会优先发现并调用 `opencode.cmd` / `codex.cmd`。
- OpenCode 如果报 `attempt to write a readonly database`，通常不是项目逻辑问题，而是 CLI 自己的本地状态目录不可写；需要在可写环境下运行。
- Codex 运行中如果先出现 websocket 连接失败、随后回退到 HTTPS，但最终退出码是 `0`，这通常仍可视为成功执行。
- 如果 reply 联调不稳定，先确认 runner 用的是 bot mailbox、发件端用的是 user mailbox；单邮箱 self-reply 仍受服务商路由影响，不再是推荐部署方式。
- `summary.md` 第一行和状态邮件里的 `Summary:` 来自启发式提取；如果某个 CLI 版本改了输出格式，优先查看 `stdout.log` / `stderr.log` 原始内容。
- 如果后端进入等待态，stdout 里必须输出完整的 `question capsule`；自由文本问题不会被识别为 `[QUESTION]`。
- 如果 `[QUESTION]` 邮件里有多题，优先按状态邮件给出的 `Answers:` 模板回复；只写一段自由文本通常不会继续执行，而是收到新的 `[QUESTION]` 提示。
- 如果普通 reply 或 `/resume` 收到“没有可恢复 native context”的状态邮件，通常说明当前 thread 既没有可恢复的 native session，又不处于支持自动 recovery 的失败态；这时应使用 `/new` 或 `/rerun`。
- 如果邮件或 snapshot 里指定了 `profile`，但 `config.yaml` 对应后端没有配置映射，本轮会失败并在错误信息里明确指出缺失的 profile 名称。
- 如果网页邮箱发出的新任务邮件只有 HTML 正文、没有有效 `text/plain`，当前版本也会自动回退解析；如果仍然看起来“没响应”，先去 `INBOX` 搜索 `[ACCEPTED]` / `[DONE]`，再检查 [_tmp_live_mail_runner/loop.stderr.log](_tmp_live_mail_runner/loop.stderr.log)。

## 后续事项

- 发出内容方向当前先按 `consumer contract freeze -> P9 HTML projection -> broader outbound convergence` 推进，不再把 neutral outbound model、summary-first plain text 或 subject-shape cutover 抢到 P9 前面
- 评估是否要让非 reply 新邮件在 `workspace + title` 明确命中时复用已有 session
- 如需继续推进会话面，优先收口 cross-workspace routing 与 non-reply reuse policy，而不是退回按标题隐式猜测
- 如果继续推进调度重构，则优先补“新邮件按 workspace + 标题复用已有 session”以及更明确的 session 定位能力
- 多媒体邮件输入输出功能的后续开发以 [docs/current/multimedia_mail_protocol.md](docs/current/multimedia_mail_protocol.md) 为唯一事实来源

## Transcript Export

可以使用仓库内脚本把某个线程的多轮对话按时间顺序导出并拼接成 transcript。

```powershell
.\.venv\Scripts\python.exe .\scripts\export_thread_conversation.py thread_013 --task-root .\_tmp_live_mail_runner\tasks
```

默认输出为 Markdown，也可以显式写入文件：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_thread_conversation.py thread_013 --task-root .\_tmp_live_mail_runner\tasks --output .\thread_013_transcript.md
```

可选参数：
- `--format markdown|json`
- `--include-empty`
- `--output <path>`

导出规则：
- 按 `mail/raw_*.json` 的顺序输出
- 用户邮件会优先提取真正的新回复内容，而不是整段引用
- 系统状态邮件会优先提取 `Reply:`、`Summary:`、`Question:` 或简化后的状态文本

## Web Search

可以通过配置项 `enable_web_search` 控制后端是否启用联网搜索能力。

- `Codex` 开启后会以顶层参数形式附加 `--search`，即 `codex --search exec ...`
- `OpenCode` 开启后会注入 `OPENCODE_ENABLE_EXA=1`
- `OpenCode` 动态权限配置里会显式允许 `websearch` / `webfetch`

当前项目里的本地运行配置已经打开：
- [mail_config.local.yaml](mail_config.local.yaml)
- [mail_config.bot.local.yaml](mail_config.bot.local.yaml)
- [_tmp_live_mail_runner/mail_config.loop_30s.yaml](_tmp_live_mail_runner/mail_config.loop_30s.yaml)
