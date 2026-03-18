# mail_task_manager 改造方案（v0.1）

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 该文档用于规划邮件适配层如何继续收口，不代表未来完整 Task Manager 平台已经在本仓库内实现。

## 0. 当前状态快照

- 评估日期：2026-03-15
- 判断依据：`state.md`、`docs/current/*`、当前自动化测试基线
- 当前基线：`.\.venv\Scripts\python.exe -m pytest -q` -> `133 passed`

按当前仓库状态，本计划各阶段应重标如下：

| 阶段 | 当前状态 | 说明 |
| --- | --- | --- |
| 阶段 A：冻结协议边界 | 已完成首轮收口 | 文档分层、`docs/current/` 与 canonical `mail_protocol.md` 已建立，reply 路由优先级与“不按标题自动复用 session”已在代码和文档中固定；剩余的是功能缺口而非文档分叉 |
| 阶段 B：收口内部实体与状态边界 | 大部分完成 | `SessionState`、`QuestionItem`、`QuestionAnswer`、`MailAttachment`、扩展后的 `ThreadState` / `RunResult` 已落地，但 `RunRequestLite` / `RunResultEnvelope` 等更显式的桥接模型仍未正式化 |
| 阶段 C：完成多问题协议闭环 | 已完成首轮落地 | 多题结构化回答、partial answers、canonical resume、`Answers:` 模板和相关测试均已落地 |
| 阶段 D：完成附件与制品协议闭环 | 已完成 MVP | inbound attachment materialization、attachment-only reply 语义、artifact manifest egress、inline image preview 和相关测试均已落地 |
| 阶段 E：补齐邮件控制面缺口 | 部分完成 | `/sessions` 已落为只读列表，但 `paused` 协议和固定 real-mailbox acceptance 仍未完成 |
| 阶段 F：为未来平台预留桥接点 | 部分完成 | `RunResult` 已具备部分桥接字段，但 typed CLI output parsing、正式 envelope 和平台级 bridge 仍未完成 |

## 1. 文档目的

本方案只针对 `mail_task_manager` 本体改造，不扩展到完整 Task Manager 平台实现。
目标是在尽量保留当前执行内核的前提下，把现有系统收口成一个**稳定的邮件控制面 / 邮件适配层**，为后续 PC、Android、外部记忆系统和统一工具协议接入留出扩展空间。

---

## 2. 改造目标

本轮改造的目标不是推倒重来，而是完成以下四件事：

1. 让邮件协议稳定可依赖。
2. 让 `workspace / session / run` 的边界更清晰。
3. 让后端执行结果和问答恢复从“启发式文本”升级为“类型化结果”。
4. 让 Android / 未来 PC 侧能够基于稳定的 mail protocol 工作，而不是依赖实现细节。

一句话定义：

> 把 `mail_task_manager` 从“邮件触发的单体任务器”改造成“以邮件为控制平面的任务执行适配层”。

---

## 3. 当前架构判断

当前实现已经具备较强的执行能力和恢复能力，不适合重写。
建议保留：

- `thread_state.json` 作为当前每个物理线程的事实来源
- `tasks/thread_xxx/` 作为运行产物目录
- `tasks/_scheduler/workspaces/<workspace_id>/` 下的调度索引
- 同 workspace 串行、跨 workspace 并发、follow-up 排队、重启恢复
- native backend resume 与 failed fallback recovery
- 现有 reply / slash command 基础能力

当前主要问题不在 runner，而在：

- mail 协议真相源已经完成首轮收口，当前以 `docs/current/mail_protocol.md` 为总入口、以专题协议文档为补充
- `thread / session / workspace` 之间职责边界仍然混合
- 结果提取与恢复输入仍有启发式路径
- 多问题协议与附件协议的代码能力已经落地，但文档权威路径和剩余验收门槛仍在收口
- Android / 未来前端需要的“稳定交互面”尚未完全固定

因此，本轮改造应以**协议收口 + 实体收口 + 类型化结果**为主，而不是重写调度器。

---

## 4. 本轮改造后的定位

改造后，`mail_task_manager` 应被明确定位为：

- 一个邮件入口与邮件出口系统
- 一个 mail-thread 控制面
- 一个面向任务执行内核的适配层
- 一个为 Android / PC / 未来 Task Manager 提供兼容协议的后端边界

它**不是**未来平台的全部；它承担的是“邮件侧控制平面”的角色。

换句话说：

- Task execution kernel 继续在现有 runner / scheduler 中
- Mail protocol 继续作为控制平面
- Android / PC 可以继续通过回复邮件协议进行交互
- 更高层的平台能力（记忆系统、tools registry、task-run-packet 等）以后逐步向上叠加

---

## 5. 改造原则

### 5.1 不重写执行核

保留当前 Phase 7 已经稳定的 runner、scheduler、native resume、failed fallback recovery。

### 5.2 先收口协议，再扩展功能

优先冻结 mail 协议、问答协议、附件协议、结果协议，不先扩写功能面。

### 5.3 邮件继续作为控制平面

不引入第二套独立控制协议。
Android / 其他端继续通过标准 reply 语义与已有线程交互。

### 5.4 默认不做隐式猜测

不以“同主题”自动串接 session 作为默认行为。
任何 session 复用与切换都应显式、可解释、可测试。

### 5.5 类型化优先于启发式

多问题恢复、结果提取、附件发送、后续工具接入，优先走显式结构而不是自由文本猜测。

---

## 6. 保留 / 调整 / 新增 / 暂缓

## 6.1 保留

以下能力直接保留，不作为本轮主要重写对象：

- `thread_state.json` 持久化结构
- `tasks/thread_xxx/` 产物目录
- `tasks/_scheduler/workspaces/...` 调度索引
- workspace 串行 / 跨 workspace 并发
- follow-up queued snapshot
- runner restart recovery
- native backend session resume
- failed-thread fresh recovery fallback
- 现有 slash command 框架

## 6.2 调整

以下能力需要重构或收口：

- mail ingest routing
- waiting-state answer parsing
- resume input synthesis
- outgoing status/result rendering
- CLI result parsing
- session control UX（尤其 `/sessions` 的作用边界）

## 6.3 新增

本轮建议新增的核心能力及当前状态：

- multi-question structured answer parsing：已落地
- canonical question-set persistence：已落地
- attachment ingress / artifact manifest egress：已落地
- typed run result envelope：部分落地
- paused protocol：未落地
- mail adapter side validation gates：部分落地

## 6.4 暂缓

本轮不建议做：

- 非邮件控制面的第二套协议
- 自动按标题复用旧 session 的默认行为
- 完整平台级 memory 工具接入
- 完整 tools registry
- Android/PC 之外的新客户端能力面
- 通用富文本 / Markdown compose
- 通用附件推断式处理

---

## 7. 目标架构（mail_task_manager 视角）

建议把 `mail_task_manager` 收口为以下五层：

### 7.1 Mail IO 层

负责：

- 收取邮件
- 解析正文与附件
- 发送状态邮件 / 回复邮件 / 带附件邮件
- 维护 reply 级别的 header 语义（In-Reply-To / References）

### 7.2 Mail Protocol 层

负责：

- 首封任务邮件解析
- reply 动作判定
- slash command 解析
- 问题回答协议解析
- attachment-only reply 语义
- `[QUESTION]` / `[DONE]` / `[FAILED]` 等邮件渲染规范

### 7.3 Session Control 层

负责：

- reply 命中已有 session
- waiting state 与 question set 状态推进
- `/status`、`/resume`、`/new`、`/kill` 等动作编排
- queued follow-up 与 session-level 路由

### 7.4 Run Compilation 层

负责：

- 从 mail action 生成规范化 run input
- 生成 canonical resume text
- 注入附件元数据、profile、permission、workspace 信息
- 为 adapter 生成统一运行上下文

### 7.5 Execution Kernel Adapter 层

负责：

- 调用 Codex / OpenCode adapter
- 解析 typed result
- 回写 run 状态、artifact、summary、test/result 字段
- 触发 reporter 生成最终状态邮件

---

## 8. 分阶段改造方案

## 阶段 A：冻结协议边界

### 当前状态

- 状态：已完成首轮收口
- 已落地：`docs/current/`、canonical `mail_protocol.md`、reply 路由优先级和“不按同标题自动复用旧 session”的边界都已固定
- 未完成：`paused` 仍未进入正式用户协议，相关缺口已转为编码阶段待完成事项

### 目标

先把 mail protocol 变成唯一权威，避免 Android、后端和文档继续分叉。

### 本阶段动作

1. 以 `TASKMAIL-MAIL-RULES.md` 为 mail 协议权威。
2. 冻结 reply 路由优先级：
   - `In-Reply-To`
   - `References`
   - state capsule
   - 主题 `[S:session_id]`
3. 明确禁止默认“同主题自动接旧 session”。
4. 冻结 Android / 其他前端允许发送的最小动作体：
   - plain-text reply
   - `/status`
   - 单题 one-tap choice
   - 多题时结构化 `Answers:`
5. 把附件协议和多问题协议升为正式协议面，而不是附加设计文档。

### 输出物

- 统一后的 `TASKMAIL-MAIL-RULES.md`
- 补充的 `QUESTION` / `Answers` / attachment / paused 子章节
- 协议约束测试

### 验收

- 协议文档不再与 Android 侧 Phase 文档冲突
- 新实现不得要求 Android invent 新 subject / 新协议
- 所有 reply 路由决策都有自动化测试

---

## 阶段 B：收口内部实体与状态边界

### 当前状态

- 状态：大部分完成
- 已落地：`SessionState`、`QuestionItem`、`QuestionAnswer`、`MailAttachment`、扩展后的 `ThreadState` / `RunResult` 以及 session/workspace 索引同步
- 未完成：`QuestionSetState`、`ReplyActionEnvelope`、`RunRequestLite`、`RunResultEnvelope` 仍未作为正式桥接模型收口

### 目标

不动执行核，但把内部概念从“thread 主导”收口成“session 控制 + thread 事实来源”。

### 本阶段动作

1. 保持 `thread_state.json` 为物理事实来源。
2. 明确 `SessionState` 是逻辑控制面状态：
   - latest_thread_id
   - latest_task_id
   - backend_session_id
   - waiting_state
   - question_set_state
   - queued_follow_up
3. 新增 `QuestionSetState` 与 `QuestionAnswer` 数据结构。
4. 新增 `ReplyActionEnvelope` / `RunRequestLite` 内部模型。
5. 在不改 runner 主流程的前提下，让 `task_compiler` 统一生成规范化 run 请求对象。

### 建议新增模型

- `QuestionItem`
- `QuestionSetState`
- `QuestionAnswer`
- `StructuredAnswerParseResult`
- `MailAttachment`
- `ArtifactManifestItem`
- `RunRequestLite`
- `RunResultEnvelope`

### 输出物

- dataclass/model 层改造
- state store 兼容迁移
- 旧 thread-state 到 session-level waiting/question 索引同步逻辑

### 验收

- 不破坏现有 133-pass 回归基线
- waiting/question 状态不再只依赖最后一个 question capsule
- 一个 session 的控制状态可以不依赖单个 thread 文本推断

---

## 阶段 C：完成多问题协议闭环

### 当前状态

- 状态：已完成首轮落地
- 已落地：structured answer parser、partial answer 持久化、canonical resolved answers、`Answers:` 模板、相关测试
- 剩余事项：主要剩 real-mailbox 固定验收和文档真相源收口，不再是核心实现缺口

### 目标

把当前“单问题优先、最后一个问题覆盖、原始回信直接 resume”升级成确定性多问题问答协议。

### 本阶段动作

1. 在 `intent_parser.py` 中实现 structured answer parser。
2. waiting state 下区分：
   - 单题：保留低摩擦自由文本
   - 多题：优先结构化 `Answers:`
3. partial answer 可持久化，不丢失已验证答案。
4. resume 输入不再使用 raw reply text，而改用 canonical resolved answers。
5. `[QUESTION]` 邮件正文中增加 copy-paste answer template。

### 重点模块

- `quote_extractor.py`
- `intent_parser.py`
- `task_compiler.py`
- `reporter.py`
- `thread_state` / `session_state` persistence

### 验收

- 单题路径保持兼容
- 多题路径支持 partial answers
- 多题未答全时不会错误 resume
- 多题答全后 resume 输入为规范化文本
- 覆盖中英文 reply、带 `---原始邮件---` 的真实回信情况

---

## 阶段 D：完成附件与制品协议闭环

### 当前状态

- 状态：已完成 MVP
- 已落地：inbound attachment 进 `workdir`、raw archive、attachment-only reply 语义、manifest egress、inline image preview、skipped-file note
- 剩余事项：以当前协议文档为准继续做细节校对和后续验收，不再作为主实现阻塞项
- 聚焦后续方案：本地文件交付与 run artifact 边界收口，见 `docs/plans/run_artifact_delivery_plan.md`
- Markdown-first 渲染与 inline 图片分层演进，见 `docs/plans/artifact_markdown_rendering_plan.md`

### 目标

把手机端/邮件端文件进出路径做成正式能力，为后续 Android/PC 扩展准备基础。

### 本阶段动作

1. inbound attachment 进入 `workdir`，同时落 raw archive。
2. attachment-only reply 语义显式化。
3. `context_layer` 暴露 materialized file paths。
4. backend 通过 `manifest.json` 声明 outgoing files。
5. reporter 支持普通附件与 inline image preview。
6. 缺失 manifest / 缺失文件时只做 skipped note，不让整封状态邮件失败。

### 重点模块

- `mail_io.py`
- `context_layer.py`
- `intent_parser.py`
- `task_compiler.py`
- `artifact_resolver.py`
- `reporter.py`

### 验收

- 图片/文档能进 active workdir
- attachment-only reply 可继续当前 session
- outgoing absolute path manifest 工作正常
- image 既可 attachment 又可 inline
- skipped-file 不导致发信失败

---

## 阶段 E：补齐邮件控制面缺口

### 当前状态

- 状态：部分完成
- 已落地：`/sessions` 已存在并保持只读语义；当前文档也明确不做隐式切换
- 未完成：`paused` 用户协议、固定 real-mailbox acceptance、如需 session targeting 的显式命令设计

### 目标

把当前仍保留但未正式落地的控制面能力补齐。

### 本阶段动作

1. 增加 `paused` 用户协议：
   - `/pause`
   - `/resume`
   - 状态邮件渲染
   - runner 可见状态流转
2. 明确 `/sessions` 的边界：
   - 先保持只读列表
   - 不马上做“隐式切换”
3. 如果后续要做 session targeting：
   - 采用显式命令/显式 session_id
   - 不用标题猜测
4. 把 real mailbox acceptance 提升为正式 gate：
   - `[QUESTION] -> ANSWER -> DONE`
   - real-backend `KILL`

### 验收

- `paused` 不再只是预留状态
- `/sessions` 语义明确，不引入模糊路由
- mailbox + backend 的关键路径有固定验收记录

---

## 阶段 F：为未来 Task Manager 平台预留桥接点

### 当前状态

- 状态：部分完成
- 已落地：`RunResult` 已带有 `changed_files`、`tests_passed`、`backend_session_id`、`artifacts_dir` 等桥接字段
- 未完成：`RunRequestLite` / `RunResultEnvelope` 正式化、CLI structured output parsing、`tools.list` / `tools.describe` / `task-run-packet` bridge 预留

### 目标

不把 mail_task_manager 直接改成平台，但在内部预留未来统一接入点。

### 本阶段动作

1. 在 runner 输入侧增加 `RunRequestLite`，其字段尽量贴近未来 `task-run-packet`。
2. 在结果输出侧增加 `RunResultEnvelope`：
   - `status`
   - `summary`
   - `changed_files`
   - `tests_passed`
   - `backend_session_id`
   - `artifacts_dir`
   - `error_type`
   - `error_message`
3. 在 adapter 层补 CLI structured output parsing。
4. 预留 `tools.list / tools.describe / task-run-packet` 的桥接点，但不在本轮完整实现。

### 验收

- 现有启发式 summary 仍可回退
- 如果 CLI 提供结构化结果，mail layer 可直接消费
- mail_task_manager 后续可被外部 task manager 包装，而不必重写后端执行层

---

## 9. 建议的实施顺序

按 2026-03-15 的当前状态，这里更适合作为“剩余工作顺序”，而不是从零开始的原始顺序。

推荐剩余工作顺序如下：

1. 收尾阶段 A：把当前 mail protocol 真相源继续收口到 `docs/current/`
2. 收尾阶段 E：补齐 `paused` 协议和固定 real-mailbox acceptance
3. 收尾阶段 F：补 typed result 与未来 bridge points
4. 评估阶段 B 剩余桥接模型是否还需要显式落地，避免为了抽象而抽象

这个顺序的好处是：

- 先把当前协议文档收口，避免“代码已落地、文档仍像草案”
- 再补用户最可见、也最缺正式验收的控制面缺口
- 最后才补平台桥接，避免把当前仓库误拉成完整平台

---

## 10. 模块级改造建议

## 10.1 `mail_runner/mail_io.py`

需要：

- 补 `paused` / 真实邮箱验收相关文档说明
- 其余附件相关 MVP 能力已落地

## 10.2 `mail_runner/intent_parser.py`

需要：

- paused / resume / status action parsing 收口
- 其余多题与 attachment-only reply 基础能力已落地

## 10.3 `mail_runner/task_compiler.py`

需要：

- `RunRequestLite` 统一生成
- 其余 canonical resume、attachment 注入和 recovery/resume 输入面已落地

## 10.4 `mail_runner/reporter.py`

需要：

- paused 状态邮件
- 其余多题/附件相关呈现能力已落地

## 10.5 `mail_runner/artifact_resolver.py`

需要：

- 维持当前 manifest / fallback / skipped artifact 行为
- 如后续补 typed result，可继续增强与 bridge 文档的对应关系

## 10.6 `mail_runner/quote_extractor.py`

需要：

- 当前重点是保持既有行为并通过回归测试守住

## 10.7 `mail_runner/adapters/*`

需要：

- typed CLI output parsing
- per-run backend permission projection（Codex 命令参数 / OpenCode run-scoped config overlay）
- 如决定正式化 bridge model，再补 structured result envelope 回填
- 兼容 heuristic fallback

补充说明：

- 显式 `Permission` 字段、缺省继承规则和 Codex / OpenCode 映射方案见 `docs/plans/backend_permission_control_plan.md`

## 10.8 `mail_runner/app.py` / 调度入口

需要：

- `paused` / real-mailbox acceptance 对应的控制面收口
- 如后续推进 session targeting，再补显式命令路径
- 保持现有 queue/recovery 语义不变

---

## 11. 不建议本轮做的架构调整

以下事项本轮不建议碰：

1. 不要把 `thread_state.json` 全面替换成 session-only 存储。
2. 不要直接把 `mail_task_manager` 改造成完整 Task Manager 平台。
3. 不要在本轮引入隐藏的同标题 session 自动复用。
4. 不要为了 Android/PC 去发明第二套邮件外控制协议。
5. 不要在结果层继续增加新的启发式文本特判，而应转向类型化返回。

---

## 12. 本轮结束后的理想状态

如果本方案完成，`mail_task_manager` 应达到以下状态：

- 协议是单一真相源
- 多问题问答是稳定能力
- 附件进出是正式能力
- `paused` 不再只是保留状态
- CLI 结果可类型化消费
- Android / 未来 PC 可以稳定依赖当前邮件控制面
- mail_task_manager 可以继续作为未来 Task Manager 平台的 mail adapter，而不需要被推翻

---

## 13. 最终建议

本轮改造不要把目标定成“功能更多”，而要定成：

> 让 mail_task_manager 成为一个协议稳定、状态清晰、结果可类型化、可被未来平台包裹的邮件控制面。

从工程收益看，最先应该做的不是附件，也不是 Android 联调，而是：

1. 协议收口
2. 状态收口
3. 多问题闭环
4. 附件闭环
5. typed result

这五步完成后，后面的 Android、PC、外部记忆系统和统一工具接入都会顺很多。
