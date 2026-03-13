# Mail-Based Task Manager

本项目当前已完成 `Phase 7` 的第一轮会话续接改造。当前已经实现 IMAP/SMTP 接入、新任务首封邮件解析、reply 线程恢复、slash 命令驱动的 reply 语义、`/resume` / `/new` / `/sessions` / `/status` / `/rerun` / `/kill` 闭环，以及真实 `OpenCodeAdapter` / `CodexAdapter` 的 CLI 薄封装。`summary.md` 第一行、`thread_state.last_summary` 和状态邮件里的 `Summary:` 现在都会优先展示真实后端输出的用户摘要，而不是固定模板文案。当前版本进一步启用了显式问题协议、`awaiting_user_input` 等待态、`[QUESTION]` 邮件、回答后继续执行、按后端分开的 `profile -> model` 映射，以及 native backend session id 落盘和 resume 路径。

## 当前能力

- 提供核心 dataclass、基础配置加载和启动自检入口
- 提供 `workspace.py`、`thread_store.py`、`state_capsule.py` 的本地状态与落盘能力
- 提供 `MockAdapter`、`Dispatcher` 和本地 demo `runner`
- 提供真实 `OpenCodeAdapter` / `CodexAdapter` CLI 薄封装，支持 `prompt.txt` 落盘、stdout/stderr 捕获、退出码回收和子进程 kill
- 提供真实后端输出摘要提取：成功时从 `stdout` 提取用户摘要，失败时从 `stderr` 提取主要错误，并同步到 `summary.md`、`result.json`、`thread_state.json` 和状态邮件
- 提供显式问题协议：后端可输出 `question capsule`，系统会将该轮结果落为 `awaiting_user_input` 并发送 `[QUESTION]`
- 提供等待态回复恢复：用户回复答案后会生成新的 snapshot 并继续执行，而不是直接丢弃等待中的任务主线
- 提供 `mail_io.py` 的 IMAP/SMTP SSL 接入
- 提供 `parser.py` 的 `[OC]` / `[CX]` 首封任务主题和正文解析
- 提供 `quote_extractor.py`、`context_layer.py`、`intent_parser.py`、`task_compiler.py` 的 reply 对话处理链
- 提供 `reporter.py` 的 `[ACCEPTED]` / `[RUNNING]` / `[DONE]` / `[FAILED]` / `[STATUS]` / `[KILLED]` / `[QUESTION]` 状态邮件正文生成
- 提供 `app.py --once` 的同步批处理路径，以及 `app.py --loop` 的单 worker 后台轮询路径
- 支持 reply 邮件通过 `In-Reply-To` / `References` / `state capsule` / `subject [S:session_id] tag` 命中原线程
- 支持 slash 命令版 reply 动作解析：`/resume`、`/new`、`/sessions`、`/status`、`/rerun`、`/kill`
- 支持 `profile` 字段在 snapshot / thread state / action 中持久化，并在真实 adapter 中按后端映射到实际 model 参数
- 支持在 thread/session/run 结果中持久化 `backend_session_id`，并在后续 `/resume` 时走 native `codex exec resume` / `opencode run --session`
- Windows 下在 `opencode_command` / `codex_command` 为空时，会优先自动发现 `opencode.cmd` / `codex.cmd`
- 支持将 `opencode_command: demo` / `codex_command: demo` 作为本地演示后端，用于不消耗真实模型额度的验证
- 已完成真实邮箱验证：Phase 2 验证了新任务 happy path；Phase 3 验证了 `NEW_TASK -> STATUS_QUERY -> KILL`；Phase 5 验证了真实邮箱入口下的 `[OC]`、`[CX]`、`STATUS_QUERY` 和 `RERUN`，并额外完成了一轮新的 live `[OC]` + `[CX]` 联调
- 已完成 Phase 6 本地问答恢复验证：`QUESTION -> ANSWER -> DONE` 和等待态 `RERUN` 拒绝路径均已由自动化测试覆盖
- 提供根目录 `state.md` 记录每个阶段完成后的当前项目状态

## 目录

```text
docs/
mail_runner/
  adapters/
  templates/
tests/
tasks/
config.example.yaml
README.md
requirements.txt
state.md
task.md
```

## 计划中的会话调度重构

当前实现仍然是以 `thread` 为中心、全局单活动任务的模型。为了更贴近 Codex / OpenCode 的实际使用方式，下一轮重构将逐步切换到 `workspace + session` 模型，并按阶段完成测试后再推进。

- `workspace` 由 `repo_path + workdir` 唯一标识
- 同一个 `workspace` 下允许存在多个 `session`
- 非 reply 新邮件在同一 `workspace` 下如果命中新的 session 标题，会自动创建新的 session
- reply 邮件只能命中已有 session，不允许在同一邮件线程里分叉出新 session
- 同一个 `workspace` 同时只允许 1 个 active session
- 系统架构会预留多个 running session 的扩展能力，初期默认仍可从单 worker 配置开始

重构规划和阶段验收要求记录在 [docs/session_scheduler_plan.md](docs/session_scheduler_plan.md)。

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
- `max_concurrent_runs: 2`
  表示全局最多允许多少个 session 同时运行。不同 `workdir` 的 session 可以并发；同一个 `workspace(repo + workdir)` 仍然保持串行。
- `auto_create_workdir: false`
  表示默认要求 `Repo + Workdir` 已存在；若设为 `true`，当 `Repo` 已存在且 `Workdir` 是仓库内的相对路径时，会在执行前自动创建该目录。

本地文件约定：

- 仓库内 `config.example.yaml` 是可提交的示例配置。
- 本地真实配置建议放在 `config.yaml` 或 `mail_config.local.yaml`，这两者都应视为本地敏感文件，不提交。
- `tasks/` 是运行态状态目录，仓库只保留 `tasks/.gitkeep` 作为空目录占位。
- `_tmp_*` 目录用于本地验证和联调产物，可按需保留排障，也可在验证完成后自行清理。
- 根目录 `.gitignore` 已包含 `.venv/`、`.pytest_cache/`、`config.yaml`、`mail_config.local.yaml`、`tasks/*` 和 `_tmp_*/`。

## 运行

推荐优先使用仓库内虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m mail_runner.app
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m mail_runner.runner --snapshot .\seed.json
.\.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml
.\\.venv\\Scripts\\python.exe -m mail_runner.app --loop --config .\\mail_config.local.yaml
.\\.venv\\Scripts\\python.exe -m mail_runner.runner --snapshot .\\seed.json --config .\\config.yaml
```

后台服务推荐直接使用仓库内脚本：

```powershell
.\scripts\start_mail_runner.cmd
.\scripts\restart_mail_runner.cmd
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 stop
```

脚本默认行为：

- 默认优先使用 `._tmp_live_mail_runner\mail_config.loop_30s.yaml`；如果不存在，则回退到仓库根目录 `mail_config.local.yaml`
- 运行态 pid、stdout/stderr 和辅助脚本都落在 `._tmp_live_mail_runner\`
- `restart` 会先停止旧的 mail-runner 进程链，再用最新代码重启后台轮询
- `status` 会列出当前 mail-runner 的 `cmd -> powershell -> python` 进程链，方便确认服务是否真的在跑

本地 demo `snapshot` 示例：

```json
{
 "backend": "opencode",
  "profile": null,
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
Subject: [OC] Refactor floor_shear

Repo: D:\proj\my_repo
Workdir: src\postprocess
Timeout: 60
Mode: modify
Profile: strong

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
/resume
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

- 新任务主题仍然使用 `[OC]` / `[CX]`
- reply 主题除了常见的 `Re:` / `FW:` / `Fwd:`，现在也兼容中文客户端常见的 `回复:` / `回复：` / `答复:` / `答复：`
- reply 命中线程时优先看 `In-Reply-To` / `References`；主题前缀兼容主要用于更稳定地做 `subject_norm` 归一化，而不是用“相同主题”强行串 session

等待提问后的回复示例：

```text
Yes, update both modules.
```

```text
Profile: fast
Use the lighter profile first, then summarize the risks.
```

## 当前限制

- 当前仍然只支持单用户、本地、单任务串行执行；`--loop` 虽然能后台跑一个任务并继续收邮件，但不引入任务队列或并发 worker
- reply 线程恢复现在只接受显式 session 线索：优先 header，再用 state capsule，最后才用 subject 里的 `[S:session_id]` 标签；不再使用“同主题自动接旧 session”的兜底策略
- 普通 reply 默认会续接当前线程对应的 native backend context；只有新发邮件或显式 `/new` 才会启动 fresh session。`[QUESTION]` 等待态回复仍允许直接回答，不强制写 `/resume`
- 邮件正文提取现在支持 `text/plain` 优先、空 plain part 自动回退 `text/html`；对只发 HTML 正文的网页邮箱更友好
- 当前的问答恢复是“应用层恢复”：后端提问后本轮退出，用户回答后生成新的 snapshot 再继续；不是同一个 CLI session 进程原地续跑
- `profile` 现在会在真实 adapter 中映射为后端自己的 `-m` 参数；如果 profile 已设置但对应映射缺失，本轮会直接失败并返回清晰错误
- 同账号给自己发 reply 是否会重新进入 `INBOX` 取决于邮箱服务商行为；当前 reply 实机验证使用了真实 IMAP 收件链路，但为了规避服务商路由差异，验证时采用了可控的 inbox injection
- 当前不会解析真实 CLI 的结构化结果，因此 `changed_files` 固定为空，`tests_passed` 固定为 `null`
- 真实后端摘要提取仍是启发式规则，不保证对所有未来 CLI 输出格式都完美
- 当前仍未把 `KILL` 纳入“真实邮箱入口 + 真实后端”的固定验收项；`KILL` 的主验证仍依赖受控长任务场景
- `paused` 目前只在状态模型里预留，没有单独的邮件协议或用户命令
- 问题识别只支持显式 `question capsule` 协议，不支持从任意自由文本里猜测“这是一条问题”

## 阶段边界说明

- Phase 1 已锁定为“本地单任务串行执行 + 文件状态落盘”主链，不为未来邮件智能逻辑提前扩大实现范围。
- Phase 3 已按最小实现完成 reply 线程恢复、quote 提取、state capsule 回复恢复、规则版自然语言动作解析，以及 `STATUS` / `RERUN` / `KILL`。
- 当前代码只做必要的可扩展准备：状态值集中定义、`runner` / `dispatcher` 接口保持薄、数据结构保留后续增字段空间。
- 当前阶段仍明确不做自动问答回合、任务挂起/恢复、OpenCode -> Codex 自动升级、复杂线程恢复、多任务并发和对话式邮件协作。
- `profile` 当前已从“字段预留”升级为“按后端配置映射到真实 model 参数”，但仍不接受 raw model id 直接出现在邮件协议里。
- Phase 4 只做真实 CLI 薄封装和演示模式，不做真实模型结果理解、问答挂起状态机或 profile 驱动模型路由。
- Phase 5 只做稳定性补强、摘要/错误信息收口、README/troubleshooting 完善，以及真实邮箱入口 + 真实 backend 联调；不实现 `[QUESTION]` 或 `awaiting_user_input` 行为。
- Phase 6 只实现显式问题协议、`awaiting_user_input`、回答后继续执行，以及 profile 映射；不实现 native CLI session resume、通用 paused 命令或多任务会话编排。

## Troubleshooting

- `python` 指向 Windows Store 占位程序时，请直接使用仓库内 `.venv\Scripts\python.exe`。
- Windows PowerShell 可能因执行策略阻止 `opencode.ps1` / `codex.ps1`；当前实现会优先发现并调用 `opencode.cmd` / `codex.cmd`。
- OpenCode 如果报 `attempt to write a readonly database`，通常不是项目逻辑问题，而是 CLI 自己的本地状态目录不可写；需要在可写环境下运行。
- Codex 运行中如果先出现 websocket 连接失败、随后回退到 HTTPS，但最终退出码是 `0`，这通常仍可视为成功执行。
- 同账号给自己发 reply 是否重新进入 `INBOX` 取决于邮箱服务商；如果 reply 联调不稳定，优先使用真实 IMAP inbox injection 做确定性验证。
- `summary.md` 第一行和状态邮件里的 `Summary:` 来自启发式提取；如果某个 CLI 版本改了输出格式，优先查看 `stdout.log` / `stderr.log` 原始内容。
- 如果后端进入等待态，stdout 里必须输出完整的 `question capsule`；自由文本问题不会被识别为 `[QUESTION]`。
- 如果邮件或 snapshot 里指定了 `profile`，但 `config.yaml` 对应后端没有配置映射，本轮会失败并在错误信息里明确指出缺失的 profile 名称。
- 如果网页邮箱发出的新任务邮件只有 HTML 正文、没有有效 `text/plain`，当前版本也会自动回退解析；如果仍然看起来“没响应”，先去 `INBOX` 搜索 `[ACCEPTED]` / `[DONE]`，再检查 [loop.stderr.log](E:/projects/mail_based_task_manager/_tmp_live_mail_runner/loop.stderr.log)。

## 下一阶段

下一阶段是 `Phase 7`，预计包括：

- native CLI session resume / continue 的可行性验证
- `paused` 的真正用户协议和恢复语义
- 真实邮箱入口 + 真实 backend 下 `[QUESTION] -> ANSWER -> DONE` 的固定验收路径
- 真实 backend 邮件链路里的 `KILL` 固定验收路径

如果后续优先执行会话调度重构，则会先按 `docs/session_scheduler_plan.md` 中定义的 3 个阶段推进，再回到上述更深层的运行时增强项。
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
- [mail_config.local.yaml](E:/projects/mail_based_task_manager/mail_config.local.yaml)
- [_tmp_live_mail_runner/mail_config.loop_30s.yaml](E:/projects/mail_based_task_manager/_tmp_live_mail_runner/mail_config.loop_30s.yaml)
