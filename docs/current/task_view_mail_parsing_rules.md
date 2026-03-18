# Task View Mail Parsing Rules

> 文档层级：Layer 1（当前仓库的外部解析规则）
>
> 当前位置：`docs/current/task_view_mail_parsing_rules.md`

## Status

- Date: 2026-03-16
- Purpose: define mailbox parsing rules for external projects that want to render one logical content view plus one logical status view
- Scope: message collection, classification, body extraction, session boundaries, projection rules, and attachment handling

## 1. Position

本规则只描述 **另一个项目如何解析当前任务邮件协议**。

它不描述：

- Android / Kotlin 代码架构
- 客户端内部数据层设计
- IMAP 缓存实现细节

本规则的核心结论是：

1. task session 的完整邮件事实源是本地 thread archive，而不是 live mailbox
2. 当前 runtime 会在发送新的 task status mail 后删除该 thread 中更早的 task status mails
3. 因此 live mailbox 中一个 task thread 正常情况下只保留最新一封 task status mail

换句话说，另一个项目可以 **显示成一张内容卡 + 一张状态卡**；如果需要完整历史，必须读取 thread archive / transcript export，而不能假设 IMAP 线程本身是 append-only。

## 2. Canonical Unit

外部项目的解析单位应是 **session**，不是“看起来主题相同的一串邮件”。

原因：

- 当前 reply 路由优先依赖 `In-Reply-To` / `References`
- 状态邮件会带 `state capsule`
- 主题里可能带 `[S:session_id]`
- `/new` 会从已有回复链中显式开启一个新的 session

因此：

- 不要仅凭主题把多封邮件硬串成一个逻辑任务
- 不要把 `/new` 后产生的新 session 继续并入旧 session 的内容投影

## 3. Session Discovery Rules

外部项目识别一个 session 时，优先级应与当前仓库保持一致。

### 3.1 Matching Priority

按以下优先级命中 session：

1. `In-Reply-To`
2. `References`
3. `state capsule`
4. 主题中的 `[S:session_id]`

### 3.2 Session Boundary

以下情况应视为新的 session，而不是当前 session 的继续：

- 首封新任务邮件，主题前缀是 `[OC]` 或 `[CX]`
- 用户显式发送 `/new`
- 当前邮件无法通过上面的优先级规则命中已有 session

### 3.3 `/new` Special Rule

`/new` 是显式“开新 session”动作。

对外部项目来说：

- 它可能发生在一个已有回复链里
- 但新 session 后续的系统状态邮件不会继续 reply 到旧链
- 因此解析时必须把 `/new` 当作 **逻辑断点**

结论：

- “邮箱 conversation view” 不一定等于 “任务 session view”

## 4. Message Classification Rules

每封邮件都应先分类，再参与投影。

### 4.1 System Message

满足以下任一条件即可判为 system message：

1. 头部 `X-Mail-Runner: 1`
2. 主题以状态标签开头，如：
   - `[ACCEPTED]`
   - `[RUNNING]`
   - `[DONE]`
   - `[FAILED]`
   - `[KILLED]`
   - `[PAUSED]`
   - `[STATUS]`
   - `[QUESTION]`
3. 正文中存在合法 `state capsule`

如果多个条件冲突，以 `X-Mail-Runner: 1` 为最高优先级。

### 4.2 New Task Mail

满足以下条件时判为 new task mail：

- 主题前缀是 `[OC]` 或 `[CX]`
- 且当前邮件不是对已有系统状态邮件的 reply

### 4.3 User Reply Mail

除 system message 与 new task mail 之外，命中当前 session 的邮件都视为 user reply mail。

### 4.4 Direct Kill Mail

主题前缀为 `[KILL] <task_id>` 的邮件是 direct kill mail。

它属于控制动作，不应并入内容投影正文。

### 4.5 Sync Control Mail

主题前缀为 `[SYNC]` 的邮件属于 project folder sync 控制邮件。

它的规则是：

- 用户发出的首封 `[SYNC]` 请求不属于 new task mail
- 系统发回的 `[SYNC] ...` 回复也不属于 task session status mail
- 这类邮件不应参与 session discovery、content projection 或 status projection
- 当前 runtime 会全局删除更早的 system-generated `[SYNC]` 回复，因此 live mailbox 正常情况下只保留最新一封 `[SYNC]` 工具回复

结论：

- 外部项目可以把 `[SYNC]` 显示为独立的系统工具邮件
- 但不要把它并入任何 task session

## 5. Ordering Rules

外部项目必须按稳定顺序处理邮件，不能只按主题分组。

推荐顺序：

1. 原始存档顺序 / IMAP UID / 服务端稳定顺序
2. 若缺失，再退化为接收时间
3. 若仍冲突，再用本地导入顺序兜底

不要只按 `Date:` 头排序，因为用户设备时间和不同邮件客户端可能不可靠。

## 6. Subject Normalization Rules

主题归一化应与当前仓库一致：

- 去掉 `Re:` / `FW:` / `Fwd:`
- 去掉中文常见前缀：`回复:` / `回复：` / `答复:` / `答复：`
- 去掉状态前缀：
  - `[ACCEPTED]`
  - `[RUNNING]`
  - `[DONE]`
  - `[FAILED]`
  - `[STATUS]`
  - `[KILLED]`
  - `[QUESTION]`
  - `[S:session_id]`

但要注意：

- 主题归一化只用于展示和弱辅助匹配
- 不能替代真正的 session 路由

## 7. Body Extraction Rules

### 7.1 User Mail Extraction

user mail 的正文提取目标是 `reply_delta`，即“本轮新增内容”。

提取规则：

1. 先去掉所有 `state capsule`
2. 再去掉所有 `question capsules`
3. 再按常见引用分隔符裁剪尾部引用内容，例如：
   - `On ... wrote:`
   - `回复:`
   - `答复:`
   - `-----Original Message-----`
   - `-----原始邮件-----`
   - 以 `>` 开头的引用行
4. 去掉首尾空行

结论：

- 外部项目不应直接把完整 reply body 当作新增上下文
- 应优先使用裁剪后的 `reply_delta`

### 7.2 System Mail Extraction

system mail 的正文提取目标不是完整正文，而是结构化状态信息。

提取顺序：

1. 解析主题状态标签
2. 解析 `state capsule`
3. 解析 `question capsules`
4. 解析正文中的人类可读段落，例如：
   - `Summary:`
   - `Reply:`
   - `Pending Questions:`
   - `Received Answers:`
   - `Paused From:`

对 system mail：

- capsule 区域是机器可读事实
- 人类可读段落是展示用摘要

## 8. State Capsule Rules

外部项目必须支持解析：

- `---TASK-STATE-BEGIN---`
- `---TASK-STATE-END---`

当前 capsule 中的核心字段包括：

- `thread_id`
- `workspace_id`
- `session_id`
- `session_name`
- `task_id`
- `backend`
- `repo_path`
- `workdir`
- `mode`
- `status`
- `last_summary`

解析规则：

- 只取最后一个完整 `state capsule`
- block 内每行按 `key: value` 解析
- 若 block 不完整或行格式非法，则整块视为无效

## 9. Question Capsule Rules

外部项目必须支持解析：

- `---TASK-QUESTION-BEGIN---`
- `---TASK-QUESTION-END---`

当前 question capsule 中的核心字段包括：

- `question_set_id`
- `question_id`
- `question_type`
- `required`
- `question_text`
- `choices`
- `choice_labels`

解析规则：

- 一封邮件里可有多个 question capsule
- 多个 block 必须共享同一个 `question_set_id`
- `choices` 用 `|` 分隔
- `choice_labels` 用 `key=value | ...` 解析
- 如果同一封邮件里出现多个不同的 `question_set_id`，整组 question capsules 视为无效

## 10. Logical Projection Model

外部项目可以把一个 session 投影成两个逻辑对象：

- `Content Projection`
- `Status Projection`

但这两个对象都必须由原始邮件链计算得出，不能反过来替代原始邮件。

## 11. Content Projection Rules

`Content Projection` 的目标是回答：

- 当前这个任务到底在做什么
- 截至现在有哪些明确约束
- 用户后来补充了哪些上下文
- 当前有哪些用户输入附件需要参考

它不是“把所有邮件全文拼接起来”，而是按规则合并出的 canonical 内容视图。

### 11.1 Base Task Spec

content projection 的起点必须来自首封 new task mail。

首封任务邮件应解析这些字段：

- `Repo:`
- `Workdir:`
- `Timeout:`
- `Mode:`
- `Profile:`
- `Permission:`
- `Task:`
- `Acceptance:`

解析规则应与当前仓库一致：

- `Repo` 必填
- `Task` 必填
- `Workdir` 可空
- `Timeout` 缺失时用默认值
- `Mode` 缺失时默认 `modify`
- `Permission` 缺失时使用 backend 默认权限

### 11.2 Task Spec Override Rules

后续 user reply 中如果显式出现这些结构化字段，应更新 content projection 的“当前任务规格”：

- `Profile:`
- `Permission:`
- `Timeout:`
- `Mode:`
- `Task:`
- `Acceptance:`

规则：

- 显式 `Task:` 视为替换当前 task body
- 显式 `Acceptance:` 视为替换当前 acceptance 列表
- 显式 `Timeout:` / `Mode:` / `Profile:` / `Permission:` 视为覆盖当前值

如果 reply 只有自然语言，没有显式 `Task:`，则不要重写 task body。

### 11.3 Context Log Rules

后续 user reply 中的自然语言增量、问题回答、恢复说明，都应进入 `context log`。

应计入 `context log` 的内容：

- 普通 free-text reply
- `/resume` 后的正文
- 单题回答
- 多题的结构化 `Answers:` 块
- 仅附件回复所代表的“新增附件输入”

不应计入 `context log` 的内容：

- 纯 `/status`
- 纯 `/sessions`
- 纯 `/pause`
- 纯 `/kill`
- 纯 `/rerun`

也就是说：

- 控制命令默认不改内容视图
- 内容视图只保留“任务语义相关”的输入

### 11.4 Multi-Question Answer in Content Projection

如果用户回复的是多题答案：

- 外部项目应保留结构化 `question_id: canonical_value` 结果
- 不应把显示 label 当成 canonical 值

推荐在 content projection 中记录为：

- `question_set_id`
- 每个 `question_id`
- 每个答案的 canonical value

这样后续项目就不需要重新从自然语言里推断答案。

## 12. Status Projection Rules

`Status Projection` 的目标是回答：

- 当前 session 现在处于什么状态
- 当前有没有等待用户回答
- 当前最新摘要是什么
- 当前下一步应该做什么

### 12.1 Latest Status Wins

status projection 以当前 session **最新的 system message** 为准。

如果存在多封 system messages：

- 只把最新一封视为当前状态
- 旧状态邮件只作为历史，不再参与当前状态展示

### 12.2 Status Sources

status projection 的事实来源优先级：

1. 最新 system mail 中的 `state capsule`
2. 最新 system mail 主题中的状态标签
3. 最新 system mail 的人类可读正文

### 12.3 Pending Questions

如果最新 system mail 带 question capsules 或正文里的 pending question 信息，则 status projection 必须包含：

- `question_set_id`
- pending questions
- required / optional
- choices
- choice labels
- 已收答案（如果正文里有 `Received Answers:`）

### 12.4 Missing Historical Status Mails

当前仓库会强制删除旧 task status mails。

因此外部项目必须接受：

- 历史状态邮件可能不完整
- 但最新存活状态邮件仍然足以表达当前状态
- 如需完整历史，应读取本地 thread archive，而不是依赖 live mailbox

结论：

- status projection 不能依赖“完整的状态邮件历史都还在邮箱里”

## 13. Attachment Parsing Rules

### 13.1 General Rule

附件是原始邮件事实的一部分，但内容投影不应把重复二进制反复内联进去。

外部项目应维护附件注册表，而不是把每轮附件正文重复展开。

### 13.2 Stable Attachment Identity

建议按以下优先级为附件建立稳定标识：

1. `sha256`
2. `Message-ID + filename + size`
3. 本地导入时生成的稳定 id

### 13.3 Repeated Filename Rule

如果同名文件在不同轮次重复出现：

- 不要直接覆盖旧附件
- 应把它视为新的附件 revision
- UI 层可只显示最新 revision
- 原始历史仍应保留全部 revision

### 13.4 User vs System Attachments

建议区分：

- user incoming attachments：属于 content projection 的输入资产
- system outgoing attachments / inline images：属于 status projection 的输出结果

结论：

- 输入附件归到内容视图
- 输出制品归到状态视图

## 14. Parsing Result Rules

另一个项目在完成一轮解析后，建议至少得到以下逻辑结果：

- `session_identity`
- `base_task_spec`
- `context_log`
- `input_attachments`
- `latest_status`
- `latest_summary`
- `pending_question_set`
- `output_artifacts`
- `raw_message_refs`

这里的 `raw_message_refs` 只是“可回看历史”的索引，不应被用户日常主视图直接展开。

## 15. Minimal External Contract

如果另一个项目只想实现最小可用版本，至少要遵守这些规则：

1. 解析单位按 session，不按主题
2. 原始邮件链保留为事实源
3. 只把“两封邮件”当作逻辑投影，不当作真实物理邮件
4. user mail 用 `reply_delta` 提取新增内容
5. system mail 用状态标签 + state capsule + question capsules 解析当前状态
6. `/new` 视为新 session 边界
7. 多题答案必须保留 `question_id -> canonical value`
8. 附件按稳定 id 做去重和 revision 管理，不要按文件名覆盖

## 16. References

本规则基于当前仓库实现与协议：

- `docs/current/mail_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `mail_runner/parser.py`
- `mail_runner/state_capsule.py`
- `mail_runner/quote_extractor.py`
- `mail_runner/context_layer.py`
- `mail_runner/transcript_export.py`
