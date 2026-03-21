# Android Reply Method Rules

> 文档层级：Layer 1（当前仓库的客户端集成规则）
>
> 当前位置：`docs/current/android_reply_method_rules.md`

## Status

- Date: 2026-03-16
- Purpose: define the recommended Android reply method for the current `mail_based_task_manager`
- Scope: reply UI, reply serialization, thread-state-aware actions, and attachment behavior

## 1. Position

Android 端在当前架构中不是独立控制面，而是 **Mail 控制面上的一个回复客户端**。

因此安卓端的回复方法必须满足两类约束：

- 对用户低摩擦：用户能直接继续、回答问题、恢复暂停、发控制命令
- 对系统可确定解析：服务端只依赖现有 mail protocol，不依赖客户端特判

本规则的目标不是让安卓端“更聪明”，而是让安卓端发出的回复邮件 **稳定命中原 session，并被当前解析器确定性消费**。

当前推荐双邮箱部署：

- Android 端只登录 `user mailbox`
- Android 端把 `bot mailbox` 当作收件人
- 不要把 `bot mailbox` 同时配置到手机上，否则仍可能引入额外客户端对系统收件箱状态的干扰

## 2. Design Goals

1. reply 必须稳定命中现有 thread / session
2. 新增内容必须能被 `reply_delta` 提取出来
3. 多问题回答必须是结构化、可校验、可恢复的
4. 安卓端 UI 可以是表单化的，但发出的邮件必须序列化为当前协议要求的纯文本
5. 附件应作为补充输入，而不是替代结构化答案

## 3. Non-Goals

当前安卓端回复方法明确不做：

- 靠“相同主题”隐式续接旧 session
- 在客户端做 NLP 式自由文本理解
- 修改或重写历史 `state capsule` / `question capsules`
- 为多问题模式发自由段落并期待服务端自动拆答案

## 4. Transport Contract

### 4.1 Headers

所有“继续当前任务”的操作都必须发成真正的 reply mail。

必须满足：

- `In-Reply-To` 指向最新一封系统状态邮件的 `Message-ID`
- `References` 继承当前线程的引用链
- `Subject` 保持原线程主题；若原主题里已有 `[S:session_id]`，必须保留

当前路由优先级是：

1. `In-Reply-To`
2. `References`
3. state capsule
4. 主题中的 `[S:session_id]`

因此安卓端应始终优先保证 `In-Reply-To` 和 `References` 正确。

### 4.2 Body Format

安卓端发出的回复邮件必须始终包含 `text/plain` 正文；`text/html` 可以作为镜像展示层，但不能成为唯一事实来源。

正文应满足：

- 第一屏只包含“本次新增内容”
- 历史引用正文放在后面
- 如果保留原始状态邮件内容，应尽量连同 `state capsule` 和 `question capsules` 一起保留

推荐做法：

- UI 上把原始引用内容折叠或只读展示
- 发送时仍把引用区拼接在正文底部

## 5. Compose Model

安卓端推荐统一成一个 `reply composer`，但内部必须区分发送模式。

推荐模式：

- `continue_session`
- `continue_target_session`
- `answer_single_question`
- `answer_multi_question`
- `resume_session`
- `command`
- `new_session_from_reply`

客户端可以使用表单或结构化 UI 收集输入，但发送前必须序列化为下面定义的纯文本格式。

## 6. State-Aware Reply Rules

### 6.1 Normal Session States

当线程状态为以下任一状态时：

- `accepted`
- `running`
- `done`
- `failed`
- `killed`

安卓端默认可以提供“继续当前线程”的回复入口，正文格式为普通续跑格式。

但应注意：

- `failed` 线程如果缺少可恢复的 native backend context，服务端可能要求用户改用 `/new` 或 `/rerun`
- `killed` 线程不应默认隐藏 `/rerun` 或 `/new`

因此安卓端在 `failed` / `killed` 态应同时提供：

- 继续回复
- `/rerun`
- `/new`

### 6.2 Awaiting User Input

当线程状态为 `awaiting_user_input` 时，安卓端不应把主发送动作设计成普通续跑。

主发送动作应切换成“回答问题”。

此状态下的规则：

- 单题：允许自然语言回答
- 多题：必须发送结构化答案
- `/rerun` 不应作为主路径；当前服务端会要求先回答问题
- `/kill` 允许保留

### 6.3 Paused

当线程状态为 `paused` 时，安卓端不能发送“隐式恢复”的普通回复。

主发送动作必须是显式恢复，即正文第一条非空行必须是：

```text
/resume
```

`paused` 状态再分两类：

- `paused` 且无 pending questions：`/resume` 后可接普通上下文
- `paused` 且仍有 pending questions：`/resume` 后应接问题答案；若是多题，仍必须用结构化答案

## 7. Plain Text Serialization Rules

### 7.1 General Rule

正文的第一段必须是本次新增内容，不要把新增内容写在引用块后面。

### 7.2 Slash Commands

如果本次回复是命令，则第一条非空行必须是 slash command。

支持的 reply commands：

- `/pause`
- `/continue <session_id>`
- `/resume`
- `/restart-runner`
- `/new`
- `/sessions`
- `/status`
- `/rerun`
- `/kill`

Targeted command note:

- Android can emit `/continue <session_id>`, `/last <session_id>`, `/status <session_id>`, `/resume <session_id>`, `/pause <session_id>`, `/end <session_id>`, and `/kill <session_id>` as plain-text first-line commands when the user wants to act on another session in the same workspace.
- Android can also emit `/restart-runner` as a plain-text first-line command when the operator wants the hosted PC-side mail loop to restart itself locally; this is a runtime-wide local control action, not a session-targeted backend action.

命令规则：

- 第一条非空行是命令
- 命令后的正文是该命令的参数或附加说明
- 不要把命令写在第二段或末尾

### 7.3 Structured Update Fields

普通回复或 `/new`、`/resume` 后的正文可以带这些结构化字段：

- `Profile:`
- `Permission:`
- `Timeout:`
- `Mode:`
- `Task:`
- `Acceptance:`

其中：

- `Task:` 后面可以是多行正文
- `Acceptance:` 后面可以是多行列表
- `Profile` 推荐只使用 profile 名称，不直接暴露 raw model id
- `Permission` 当前只支持 `default` / `highest`
- 如果省略 `Permission:`，服务端会继承当前 thread/session 已持久化的权限值

## 8. Reply Modes and Templates

### 8.1 Continue Session

适用状态：

- `accepted`
- `running`
- `done`
- `failed`
- `killed`

推荐正文：

```text
请继续把日志目录也整理一下，并说明为什么这样改。
```

带参数的正文：

```text
Profile: strong
Permission: highest
Timeout: 120
Task:
请继续把日志目录也整理一下，并说明为什么这样改。

Acceptance:
1. 不改 public API
2. 给出简短说明
```

### 8.2 Resume Session

适用状态：

- `paused`

推荐正文：

```text
/resume
Permission: highest
请继续上一轮。
```

### 8.2A Continue Target Session

适用场景：

- 用户当前在某个 workspace 的任意 thread 里
- 但想直接继续同 workspace 下的另一个 session

推荐正文：
```text
/continue thread_002
Permission: highest
请继续上一轮，并补上测试。
```

说明：

- targeted continuation 的后续 `[ACCEPTED]` / `[RUNNING]` / `[DONE]` 会继续出现在目标 session 自己的邮件链上
- Android 端可以把它做成显式“切换并继续”入口，但不要做按标题的隐式猜测

### 8.3 New Session From Reply

适用场景：

- 用户希望从当前对话引用上下文，但显式开启 fresh session

推荐正文：

```text
/new
Permission: default
Timeout: 120
Mode: analysis_only
Task:
Only analyze the issue and list risks.
```

### 8.4 Single-Question Answer

适用状态：

- `awaiting_user_input`
- `paused` 且 `paused_from_status == awaiting_user_input`

单题允许自然语言回答：

```text
Yes, update both files.
```

也允许显式恢复后回答：

```text
/resume
Yes, update both files.
```

如果客户端允许切换档位，可在正文前加：

```text
Profile: fast
Permission: highest
Yes, update both files.
```

### 8.5 Multi-Question Answer

多题模式下，安卓端必须发送结构化答案。

推荐正文：

```text
Answers:
phase2_entry_position: below
phase2_icon_strings: provide
phase2_k9_support: thunderbird_only
phase2_device_validation: acceptable
```

如果线程处于 `paused`，则应发送：

```text
/resume
Answers:
phase2_entry_position: below
phase2_icon_strings: provide
phase2_k9_support: thunderbird_only
phase2_device_validation: acceptable
```

## 9. Multi-Question Rules

### 9.1 Canonical Values

安卓端 UI 可以向用户显示友好标签，但发信时必须发送 canonical key。

例如：

- 展示给用户：`账户列表下方（设置附近）`
- 实际发出：`below`

### 9.2 Allowed Format

多题回复只允许发送当前 waiting state 中仍然有效的 `question_id`。

推荐格式：

```text
Answers:
question_id_1: value_1
question_id_2: value_2
```

禁止依赖以下写法：

- “同上”
- “第二个”
- “就按你说的”
- 一个长段落里混合多个答案

### 9.3 Client Validation

安卓端在多题模式下应在发送前完成本地校验：

- 每个必答题都已填写
- 每个 choice 类型答案都能映射到合法 canonical key
- 不发送未知 `question_id`

当前服务端支持“部分答案累计保存”，但这应视为容错而不是主交互。

因此推荐策略是：

- 默认要求用户答完所有 required questions 才允许发送
- 若未来需要支持“先保存部分答案”，应在 UI 上明确提示剩余未答项

## 10. Attachment Rules

### 10.1 General

附件应作为补充输入发送，不应破坏文本协议。

安卓端应发送标准 MIME 附件，并继续按当前 reply mode 生成正文。

### 10.2 Attachment-Only Reply

当前服务端行为是：

- 普通线程里，attachment-only reply 默认视为 `CONTINUE_SESSION`
- 单题 waiting state 里，attachment-only reply 默认视为 `ANSWER_QUESTION`

因此安卓端可以支持 attachment-only reply，但只建议用于：

- 普通续跑
- 单题要求用户补图、补文件的场景

### 10.3 Multi-Question + Attachments

多题 waiting state 下，不应让用户只发附件而不发结构化答案。

推荐规则：

- 如果当前是多题等待态，发送按钮前必须有结构化答案正文
- 附件只能作为附加输入，不能替代 `Answers:` 块

## 11. Composer UX Recommendations

安卓端推荐采用“编辑区 + 只读引用区”的双层模型。

编辑区负责：

- 普通文本输入
- slash command 输入
- 结构化问题表单
- `Profile / Timeout / Mode / Task / Acceptance` 表单
- 附件选择

只读引用区负责：

- 显示上一封系统状态邮件摘要
- 显示 `Session ID / Task ID / Status`
- 显示问题列表和可选项
- 折叠展示原始邮件引用体

发送前的序列化原则：

1. 根据当前线程状态选择 reply mode
2. 将 UI 输入序列化为当前模式要求的纯文本正文
3. 如果是 reply mail，补齐 `In-Reply-To` 和 `References`
4. 如启用引用区，则把原始状态邮件正文追加到末尾

## 12. Recommended Client Policy

为了和当前服务端保持一致，安卓端推荐默认采用以下策略：

1. 回复入口只允许从现有状态邮件进入，不允许靠“同主题新邮件”续接
2. `awaiting_user_input` 时主按钮显示“回答并发送”
3. `paused` 时主按钮显示“恢复并发送”，并自动插入 `/resume`
4. 多题用表单收集，但发送时转成 `Answers:` 文本
5. choice 题展示 label，发送 canonical key
6. `failed` / `killed` 态保留 `/rerun` 和 `/new`
7. 始终发送 `text/plain`
8. 尽量保留原始引用内容，尤其是状态邮件中的 capsule 区域

## 13. Minimal Send Checklist

安卓端在实际发信前至少应检查：

- 当前消息是 reply，不是同主题新邮件
- `In-Reply-To` 已设置
- `References` 已设置
- `text/plain` 正文非空，或当前模式允许 attachment-only
- 若是多题模式，正文包含合法 `Answers:` 块
- 若是 `paused` 模式，正文第一条非空行是 `/resume`
- 若是命令模式，正文第一条非空行是对应命令

## 14. References

本规则基于当前项目实现与协议文档：

- `docs/current/mail_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `mail_runner/intent_parser.py`
- `mail_runner/task_compiler.py`
- `mail_runner/reporter.py`
- `mail_runner/quote_extractor.py`
