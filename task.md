你要为我开发一个**自用测试版邮件驱动任务分发器**。这是一个单用户、本地运行、低复杂度系统。目标不是做平台，而是做一个能稳定使用的最小工具。

# 1. 项目目标

构建一个本地 Python 项目，实现以下能力：

1. 轮询一个邮箱（IMAP）读取任务邮件
2. 按邮件主题前缀把任务分发给两个后端之一：
   - `[OC]` -> OpenCode
   - `[CX]` -> Codex
   - `[KILL] <task_id>` -> 终止任务
3. 支持“首封邮件创建任务 + 后续回复邮件沿用线程上下文并更新任务”
4. 支持系统回邮件汇报状态：
   - `[ACCEPTED]`
   - `[RUNNING]`
   - `[DONE]`
   - `[FAILED]`
   - `[KILLED]`
   - `[STATUS]`
5. 本地用文件系统保存所有状态，不使用数据库
6. 串行执行任务，一次只跑一个任务
7. 第一版只支持单机、单用户、自用，不追求高安全性、泛用性和分布式能力

# 2. 非目标（不要做）

以下内容全部不要做，除非我后续明确要求：

- 不做 Web UI
- 不做数据库
- 不做多用户
- 不做自动路由判断
- 不做 OpenCode -> Codex 自动升级
- 不做自动 push / merge
- 不做复杂权限系统
- 不做分布式 worker
- 不做复杂队列系统
- 不做浏览器自动化
- 不做复杂附件理解
- 不做一个线程下多个并行子任务
- 不做通用聊天机器人
- 不做“智能体平台”

# 3. 运行环境约束

请默认以下假设：

- Python 3.11+
- Windows 本机优先兼容
- 使用本地文件系统保存状态
- 通过 IMAP 收邮件，SMTP 发邮件
- 后端工具通过命令行调用
- 第一版允许轮询邮箱，不要求 webhook
- 第一版允许用 `.env` 或 `config.yaml` 保存配置
- 所有路径都应尽量兼容 Windows

# 4. 核心设计原则

必须遵守：

1. **本地状态文件才是真相来源**
   - 邮件线程是辅助
   - 邮件里的状态块（state capsule）是辅助
   - 自然语言引用历史只做兜底
2. **首封邮件半结构化，后续回复自由文本**
3. **每个线程只对应一个任务主线**
4. **串行执行**
5. **先把骨架做稳，再接真实后端**
6. **先可测试，再可用，再可优化**
7. **代码不要过度抽象**
8. **只做最小可行实现**

# 5. 期望目录结构

请按这个结构实现，允许小范围微调，但不要大改：

mail_runner/
  app.py
  config.py
  models.py
  mail_io.py
  parser.py
  thread_store.py
  quote_extractor.py
  state_capsule.py
  context_layer.py
  intent_parser.py
  task_compiler.py
  dispatcher.py
  workspace.py
  reporter.py
  runner.py
  adapters/
    base.py
    mock_adapter.py
    opencode_adapter.py
    codex_adapter.py
  templates/
    opencode_prompt.txt
    codex_prompt.txt
  tests/
  tasks/
  README.md
  requirements.txt

# 6. 数据模型要求

请至少定义以下 dataclass / model（可用 dataclass 或 pydantic，但优先 dataclass + 手写校验，保持轻量）：

## 6.1 MailEnvelope
字段建议：
- message_id: str
- subject: str
- from_addr: str
- to_addr: str
- date: datetime | str
- in_reply_to: str | None
- references: list[str]
- body_text: str
- raw_headers: dict[str, str]

## 6.2 ParsedMailAction
表示这封邮件想做什么。
字段建议：
- action: Literal[
    "NEW_TASK",
    "UPDATE_TASK",
    "APPEND_CONTEXT",
    "STATUS_QUERY",
    "RERUN",
    "KILL",
    "UNKNOWN"
  ]
- confidence: float
- backend: Literal["opencode", "codex"] | None
- task_text_delta: str | None
- acceptance_delta: list[str] | None
- timeout_minutes: int | None
- mode: Literal["modify", "analysis_only"] | None
- raw_user_text: str
- notes: str | None

## 6.3 TaskSnapshot
表示当前一次可执行快照。
字段建议：
- task_id: str
- thread_id: str
- backend: Literal["opencode", "codex"]
- repo_path: str
- workdir: str | None
- task_text: str
- acceptance: list[str]
- timeout_minutes: int
- mode: Literal["modify", "analysis_only"]
- attachments: list[str]
- created_at: str
- updated_at: str

## 6.4 ThreadState
表示线程主状态。
字段建议：
- thread_id: str
- root_message_id: str
- latest_message_id: str
- subject_norm: str
- backend: Literal["opencode", "codex"]
- repo_path: str
- workdir: str | None
- current_task_id: str
- last_task_snapshot_file: str
- status: Literal["idle", "accepted", "running", "done", "failed", "killed"]
- history_files: list[str]
- last_summary: str | None
- created_at: str
- updated_at: str

## 6.5 RunResult
字段建议：
- task_id: str
- thread_id: str
- backend: Literal["opencode", "codex"]
- status: Literal["success", "failed", "killed"]
- exit_code: int | None
- started_at: str
- finished_at: str | None
- stdout_file: str
- stderr_file: str
- summary_file: str | None
- artifacts_dir: str | None
- changed_files: list[str]
- tests_passed: bool | None
- error_message: str | None

# 7. 邮件协议要求

## 7.1 新任务邮件
主题必须以以下前缀之一开头：
- `[OC]`
- `[CX]`

首封邮件建议支持这种半结构化格式：

Repo: D:\proj\my_repo
Workdir: src\postprocess
Timeout: 60
Mode: modify

Task:
把 floor_shear.py 重构为 dataclass 风格，保持现有输出不变

Acceptance:
1. pytest tests/test_floor_shear.py 通过
2. 不改 public API
3. 输出简短修改说明

要求：
- 解析器应尽量宽容
- `Repo:` 和 `Task:` 是最关键字段
- 缺少 `Workdir` 时允许为空
- 缺少 `Timeout` 时使用默认值
- 缺少 `Mode` 时默认 `modify`
- `Acceptance` 允许为空列表

## 7.2 回复邮件
后续回复邮件不要求严格格式，允许自然语言，例如：
- “先不要改代码，只分析问题”
- “补充一点，这个脚本会被 report_main.py 调用”
- “timeout 改成 120 分钟”
- “现在状态如何？”
- “重新跑一次”
- “终止当前任务”

回复邮件需要通过：
- `In-Reply-To`
- `References`
- 规范化后的 subject

解析到原线程。

# 8. 状态块（state capsule）要求

每次系统回邮件时，在正文底部追加一个机器可读但人类也能看懂的状态块，格式如下：

---TASK-STATE-BEGIN---
thread_id: thread_001
task_id: 20260311_153000_a1b2
backend: opencode
repo_path: D:\proj\my_repo
workdir: src\postprocess
mode: modify
status: done
last_summary: 已完成 dataclass 重构，public API 未改动
---TASK-STATE-END---

要求：
- 提供 `render_state_capsule(state)` 和 `parse_state_capsule(text)`
- 回复邮件解析时优先读取状态块
- 状态块缺失时再回退到本地状态和线程头信息

# 9. 本地文件组织要求

请设计一个简单稳定的文件组织方式：

tasks/
  thread_001/
    thread_state.json
    snapshots/
      20260311_153000_a1b2.json
      20260311_160000_b2c3.json
    runs/
      20260311_153000_a1b2/
        prompt.txt
        stdout.log
        stderr.log
        result.json
        summary.md
        artifacts/
    mail/
      raw_001.json
      raw_002.json

要求：
- 每个线程一个目录
- `thread_state.json` 保存该线程当前主状态
- 每次执行生成一个 snapshot 文件
- 每次运行生成一个 runs 子目录
- 原始邮件解析结果保存到 mail/ 下，便于排错

# 10. 模块职责要求

## 10.1 mail_io.py
负责：
- IMAP 拉取未处理邮件
- SMTP 发送状态邮件
- 尽量封装为 `MailClient`

至少实现：
- `fetch_unseen_messages() -> list[MailEnvelope]`
- `send_mail(to_addr, subject, body, attachments=None, in_reply_to=None, references=None)`

## 10.2 parser.py
负责：
- 新任务首封邮件的半结构化解析
- 从主题前缀中识别 backend 和 kill 指令

至少实现：
- `parse_subject(subject) -> dict`
- `parse_initial_task(body_text) -> dict`

## 10.3 quote_extractor.py
负责：
- 从 reply 邮件中提取本次新增正文
- 尽量裁掉旧引用内容

至少支持：
- `On ... wrote:` 截断
- `>` 引用块截断
- 系统状态块之前截断或单独解析
- 常见英文回复模板的粗略处理

不要追求完美，先做一个够用版。

## 10.4 state_capsule.py
负责：
- 渲染状态块
- 解析状态块

## 10.5 thread_store.py
负责：
- 按 message-id / in-reply-to / references / subject_norm 建立线程映射
- 读写 `thread_state.json`
- 保存 raw mail

至少实现：
- `resolve_thread(envelope) -> thread_id | None`
- `create_thread(...)`
- `load_thread_state(thread_id)`
- `save_thread_state(state)`

## 10.6 context_layer.py
负责：
- 组合本次新增正文 + 本地线程状态 + 邮件状态块
- 生成“本轮解析上下文”

## 10.7 intent_parser.py
负责：
- 把回复邮件自然语言解析为 `ParsedMailAction`

第一版允许：
- 先用规则 + 关键词实现
- 不必一开始接 LLM
- 规则优先

至少支持识别：
- NEW_TASK
- UPDATE_TASK
- APPEND_CONTEXT
- STATUS_QUERY
- RERUN
- KILL
- UNKNOWN

## 10.8 task_compiler.py
负责：
- 用 `ParsedMailAction` + `ThreadState` 合成新的 `TaskSnapshot`

规则要清晰：
- UPDATE_TASK：允许覆盖 task_text / acceptance / timeout / mode
- APPEND_CONTEXT：把新增内容附加到 task_text 末尾或 notes 中
- STATUS_QUERY：不生成新运行
- RERUN：复用最近 snapshot
- KILL：不生成新 snapshot，仅请求停止当前运行

## 10.9 dispatcher.py
负责：
- 根据 `TaskSnapshot.backend` 选择 adapter
- 统一返回 `RunResult`

## 10.10 workspace.py
负责：
- 创建目录
- 保存 snapshot / result / summary / logs
- 返回路径

## 10.11 reporter.py
负责：
- 生成 `[ACCEPTED]` / `[RUNNING]` / `[DONE]` / `[FAILED]` / `[STATUS]` / `[KILLED]` 邮件正文
- 自动附加状态块

## 10.12 runner.py
负责：
- 串行任务执行调度
- 管理当前运行中的子进程
- 提供 kill 能力

## 10.13 adapters/*
负责：
- 统一后端执行接口

基础接口：

class WorkerAdapter:
    def run(task: TaskSnapshot, run_dir: str) -> RunResult:
        ...
    def kill(task_id: str) -> bool:
        ...

要求：
- 第一阶段先实现 `MockAdapter`
- 后续再实现 `OpenCodeAdapter` 和 `CodexAdapter`

# 11. 后端 adapter 要求

## 11.1 第一版先做 MockAdapter
行为：
- 读取 task
- 等待 1-2 秒
- 生成虚拟 stdout/stderr/result/summary
- 返回 success

目的是在不依赖真实 OpenCode/Codex 的情况下打通整条链路。

## 11.2 OpenCodeAdapter / CodexAdapter
先做“可插拔骨架”，真实命令行集成放后期阶段。

要求：
- 通过配置读取命令名
- 支持在指定 repo_path/workdir 下运行
- 将 prompt 写入 `prompt.txt`
- 捕获 stdout/stderr 到日志文件
- 能返回退出码
- `kill()` 至少能终止当前子进程

不要一开始绑定某个复杂外部协议。优先做成“调用本地命令”的薄封装。

# 12. Prompt 模板要求

`templates/opencode_prompt.txt`：
偏执行型，强调局部修改、少做设计、尽量保持 API 不变。

`templates/codex_prompt.txt`：
偏理解型，强调先理解上下文、说明关键权衡、输出风险点。

实现时请支持用 `TaskSnapshot` 填充模板。

# 13. 主循环要求

`app.py` 需要有一个本地轮询主循环，行为如下：

1. 轮询收件箱
2. 解析新邮件
3. 保存 raw mail
4. 解析线程
5. 生成 action / snapshot
6. 对于：
   - NEW_TASK / UPDATE_TASK / APPEND_CONTEXT / RERUN：进入执行流程
   - STATUS_QUERY：仅回状态邮件
   - KILL：终止当前任务并回信
7. 串行执行一个任务
8. 回写状态和结果
9. 发回邮件

第一版可接受的简单策略：
- 每次轮询只处理一批未读邮件
- 串行消费
- 无需复杂锁
- 当前运行中的 task_id 可保存在内存 + 简单文件标记

# 14. 配置要求

请提供一种简单配置方式，优先：
- `config.yaml`
- `.env`

配置项至少包括：

- imap_host
- imap_port
- imap_user
- imap_password
- smtp_host
- smtp_port
- smtp_user
- smtp_password
- poll_seconds
- task_root
- default_timeout_minutes
- opencode_command
- codex_command
- from_name
- from_addr

# 15. 测试要求

请为每个阶段补充对应的 pytest 测试，至少覆盖：

1. subject 前缀解析
2. 初始任务正文解析
3. quote 提取
4. state capsule 渲染与解析
5. 线程解析与状态保存
6. action 识别
7. task_compiler 合成逻辑
8. MockAdapter 跑通
9. reporter 生成邮件正文
10. 整体最小 happy path

# 16. 分阶段开发要求（非常重要）

你不要一次性实现全部。必须严格按阶段推进。每个阶段结束后：
- 停止继续编码
- 给出变更文件列表
- 给出测试命令
- 给出本地手工验证步骤
- 给出已知问题
- 等我确认后再进入下一阶段

## Phase 0：初始化与骨架
目标：
- 建立项目目录
- 建立 requirements.txt / README.md
- 定义核心 dataclass / model
- 建立 config 加载
- 建立基础日志
- 建立空模块和接口骨架

验收标准：
- 项目可启动
- 所有模块可 import
- 基础测试通过

## Phase 1：本地状态层与 Mock 执行链
目标：
- 实现 workspace.py
- 实现 thread_store.py 的本地读写骨架
- 实现 state_capsule.py
- 实现 MockAdapter
- 实现 dispatcher.py
- 能从本地构造一个 TaskSnapshot 并跑完整执行链，生成 result/log/summary

验收标准：
- 不接邮件也能本地跑通
- 能生成 runs 目录和 result.json
- pytest 对应测试通过

## Phase 2：邮件收发与新任务创建
目标：
- 实现 mail_io.py
- 实现 parser.py 对新任务首封邮件的解析
- 实现 app.py 轮询和 NEW_TASK happy path
- 收到 `[OC]` / `[CX]` 首封任务邮件后，能创建线程、生成 snapshot、调用 MockAdapter、发回 `[ACCEPTED]` `[RUNNING]` `[DONE]`

验收标准：
- 用测试邮箱真实跑通一次
- 原始邮件保存到 mail/ 目录
- thread_state.json 正确生成

## Phase 3：回复邮件上下文与状态查询
目标：
- 实现 quote_extractor.py
- 实现 context_layer.py
- 实现 intent_parser.py（先规则版）
- 实现 task_compiler.py
- 支持回复邮件：
  - UPDATE_TASK
  - APPEND_CONTEXT
  - STATUS_QUERY
  - RERUN
  - KILL

验收标准：
- 能基于 reply 邮件找到原线程
- 能根据自然语言回复更新 snapshot
- 能查询状态
- 能终止 Mock 任务
- 对应测试通过

## Phase 4：真实后端 adapter 骨架
目标：
- 实现 OpenCodeAdapter / CodexAdapter 基础版
- 通过配置调用本地命令
- 将 prompt 写入文件
- 记录 stdout/stderr
- 支持 kill 当前子进程

注意：
- 这里只做薄封装，不做复杂集成
- 可以先提供一个“演示命令”模式，便于手工测试

验收标准：
- 能在本地配置命令并执行
- logs 能落盘
- kill 可用
- 与 MockAdapter 共存

## Phase 5：稳定性补强与手工测试文档
目标：
- 修补明显 bug
- 完善错误信息
- 完善 README
- 加入最小使用说明
- 给出真实使用邮件示例
- 给出 troubleshooting

验收标准：
- 我能按 README 独立跑起来
- 我能按示例邮件完成首封任务和后续回复
- 关键路径测试基本覆盖

# 17. 每个阶段的输出格式要求

每个阶段完成后，必须按下面格式回复我：

## 完成内容
- 列出本阶段实现了什么

## 修改文件
- 列出新增/修改文件

## 运行方式
- 给出启动命令
- 给出测试命令

## 手工验证步骤
- 给出 3~8 步验证说明

## 已知限制
- 列出当前阶段尚未解决的问题

## 建议下一阶段
- 只说明下一阶段做什么，不要直接开始做

# 18. 编码风格要求

1. 优先清晰，不要炫技
2. 适度类型标注
3. 不要过度设计
4. 避免大而空的抽象层
5. 模块职责单一
6. 日志足够排错
7. 错误处理尽量直白
8. 测试优先覆盖核心路径
9. 代码注释应说明“为什么”，而不是重复“做了什么”

# 19. 现在请做的事情

现在只执行 **Phase 0**，不要进入下一个阶段。
请先给出：
1. 你对需求的简短复述
2. 你准备新增/修改的文件列表
3. 然后开始编码
4. 完成后严格按“每个阶段的输出格式要求”汇报