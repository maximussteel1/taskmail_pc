# Project Folder Sync Entry Plan

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 该文档描述一个尚未实现的首封只读邮件入口，用于降低 Android / 手机端第一次发任务邮件时的路径输入成本。

## Status

- Date: 2026-03-16
- Scope: first-mail folder inventory sync for `D:\projects` and `E:\projects`
- Relation: planned feature only; current code and current protocol 尚未支持该入口

## 1. 问题定义

当前首封任务邮件要求用户直接填写：

- `Repo:`
- 可选 `Workdir:`
- `Task:`

这在 PC 上问题不大，但在手机端第一次进入时，`Repo` 或 `Workdir` 里的文件夹名输入成本很高。

当前协议只有：

- 首封新任务邮件：`[OC]` / `[CX]`
- reply 控制动作：`/status`、`/sessions`、`/resume` 等

这意味着：

- 用户在还没有任何 thread / session 的时候，没有一个“先查可用项目目录”的低摩擦入口
- Android 端即使做了更省心的 UI，首次仍会卡在“项目目录名怎么录入”这一步

## 2. 目标

本方案要新增一个 **首封只读控制入口**，用于同步当前机器上的项目根目录列表，帮助手机端先完成“选目录”，再进入真实任务创建。

v1 目标固定为：

1. 用户可以直接发送一封同步请求邮件，且这封邮件可以是第一次交互
2. 系统回复 `D:\projects` 与 `E:\projects` 下的一级子文件夹列表
3. 回复结果足够适合手机端阅读、复制和后续选择
4. 整个过程不触发 backend run，不创建 task，不占用 scheduler

## 3. 非目标

v1 明确不做：

- 递归扫描更深层目录
- 列出文件，只列出文件夹
- 允许用户通过邮件任意指定扫描根路径
- 自动根据同步回复直接创建任务
- 在同步回复里引入 session switch 或 repo shortcut 选择协议
- 为每个目录生成短编号并允许后续仅回复编号

最后一点是刻意暂缓，不是遗忘；它可能是后续 Android 端进一步降低输入成本的第二阶段能力。

## 4. 提议的邮件入口

### 4.1 入口类型

该能力应定义为 **新的首封非 reply 控制动作**，而不是 reply 扩展。

原因：

- 用户第一次发信时还没有可 reply 的系统状态邮件
- 该动作是只读查询，不应伪装成任务执行
- 该动作不应依赖 `thread/session` 已存在

### 4.2 请求格式

v1 canonical 请求建议为：

```text
Subject: [SYNC]
Body: 可为空
```

兼容性建议：

- 解析器可容忍 `[SYNC]` 后面带少量说明文字
- 但 v1 不从正文读取任何路径参数
- `In-Reply-To` / `References` 不是必需条件

### 4.3 协议位置

这个入口仍属于 **Mail 控制面**，但它不是：

- 新任务邮件
- reply continuation
- backend 指令代理

更准确地说，它是一个 **mail control plane 内的只读环境发现动作**。

## 5. 返回内容定义

### 5.1 返回原则

系统收到同步请求后，应直接回复发件人一封信息邮件。

这封回复应满足：

- 明确是系统回复
- 保持为对该请求邮件的真正 reply
- 不触发任务创建
- 不要求用户理解 thread/session 细节
- 结果可直接用于后续复制 `Repo:` 路径
- 不携带 task 专用的 `state capsule` 或 `question capsules`

### 5.2 建议主题

建议主题形态：

```text
[SYNC] Project Folder List
```

这里不建议复用 `[STATUS]`，原因是：

- 当前 `[STATUS]` 已经属于 task session 状态邮件标签
- 外部 parser 和未来 Android 侧更容易把它误认成任务状态推进
- 该邮件本质上是同步结果，不是 task 状态更新

因此更稳妥的做法是保留 `[SYNC]` 作为独立控制面主题空间。

### 5.3 建议正文结构

建议正文包含以下几段：

1. 简短说明：本次只完成项目目录同步，没有创建任务
2. 本次扫描时间
3. 每个根路径的可用性摘要
4. 每个根路径下的一级文件夹列表
5. 下一步提示：如果要正式发任务，请另起一封 `[OC]` 或 `[CX]` 邮件

建议示例：

```text
Project folder sync completed. No task was created.

Scanned roots:
- D:\projects | available | 12 folders
- E:\projects | available | 34 folders

D:\projects
- demo_repo | D:\projects\demo_repo
- legacy_tool | D:\projects\legacy_tool

E:\projects
- mail_based_task_manager | E:\projects\mail_based_task_manager
- another_repo | E:\projects\another_repo

To start a task, send a new [OC] or [CX] mail and copy one path into Repo:.
```

### 5.4 列表粒度

v1 固定规则：

- 只返回一级子目录
- 不递归
- 不列文件
- 每个根路径独立报告
- 目录列表按名称稳定排序，建议大小写不敏感排序

为了方便手机端后续复制，单个条目建议同时展示：

- 文件夹短名
- 绝对路径

## 6. 根路径与边界

### 6.1 v1 根路径范围

对外协议固定为两个项目根：

- `D:\projects`
- `E:\projects`

实现上可以由 allowlist 配置驱动，但默认行为必须等价于这两个根。

### 6.2 不可用根路径

如果某个根路径不存在、不可访问或枚举失败，回复中应显式写出该根的状态，而不是整封邮件失败。

例如：

```text
- D:\projects | unavailable | path does not exist
```

### 6.3 信息暴露边界

这个入口必须保持保守：

- 只暴露 allowlist 根路径下的一级目录名
- 不回传文件名
- 不回传目录内容摘要
- 不允许通过邮件请求额外根路径
- 不调用 backend，也不让 backend 代为扫描

## 7. 与当前协议的关系

该入口应与当前 mail protocol 保持以下边界：

1. 它是 **首封邮件可用** 的控制动作
2. 它不替代 `[OC]` / `[CX]` 的新任务入口
3. 它不改变 reply 路由优先级
4. 它不引入“同主题自动接续 session”
5. 它不把 Android 端变成第二套控制协议

换句话说：

- 手机端先用 `[SYNC]` 拿项目目录清单
- 真正发任务时仍然使用现有 canonical 新任务协议

## 8. Android 端使用方式

这个能力的价值主要在 Android / 手机端首次进入流程：

1. 用户点击“同步项目列表”
2. Android 发出一封 `[SYNC]` 邮件
3. 系统返回两个根路径下的一级目录清单
4. Android 展示清单，供用户查看、复制，或后续做 picker
5. 用户再发真正的 `[OC]` / `[CX]` 任务邮件

v1 的关键收益是：

- 先解决“目录名很难输”的痛点
- 不要求 Android 端在第一阶段就实现完整 repo 选择协议

## 9. 实现建议

推荐实现方向如下：

1. `mail_runner/parser.py`
   - 新增 `[SYNC]` 首封主题动作解析
2. `mail_runner/app.py`
   - 在 direct control fast path 中新增同步请求处理
   - 该路径不得调用 `SerialTaskRunner` 的 run dispatch
3. 新增独立目录枚举 helper
   - 负责固定根路径枚举、排序、错误收口
4. 状态邮件渲染
   - 可先用一份简单的 plain-text / html 只读回复
   - 不必强行挂到现有 task-thread reporter 上
   - 不应附带 `state capsule` / `question capsules`
5. 测试
   - 首封 `[SYNC]` 请求会被消费
   - 不触发 backend
   - 只返回一级目录
   - 文件不会出现在列表里
   - `D:\projects` 或 `E:\projects` 不可用时仍能回信

## 10. 建议的代码边界

为了避免把这个只读入口错误地塞进任务执行主路径，建议保持以下边界：

- 不创建 `TaskSnapshot`
- 不创建新的 runnable `thread/session`
- 不写入 run artifact 流程
- 不写 `artifact_index.json`
- 不占用 workspace 调度槽位

是否为这类控制邮件单独保留本地审计存档，可以在编码阶段决定；但无论如何，不应把它伪装成普通任务线程。

## 11. 验收标准

当后续开始编码时，建议以以下结果作为 v1 完成标准：

1. 用户发送首封 `[SYNC]` 邮件，即使正文为空，也能收到系统回复
2. 回复中同时报告 `D:\projects` 与 `E:\projects`
3. 每个根只列一级目录，不递归，不列文件
4. 不可用根路径会被显式标记，而不是导致整次请求失败
5. 同步请求不会启动 Codex / OpenCode，也不会创建任务
6. 用户仍需另起 `[OC]` / `[CX]` 邮件来正式创建任务

## 12. 后续阶段

如果 v1 生效，后续可以按顺序评估：

1. 在 Android 端把同步结果直接做成 repo picker
2. 为目录项生成短 id / 短标签
3. 把 `Repo:` 自动回填到首封任务模板
4. 再评估是否需要同步 `Workdir` 候选

这些都应建立在 v1 的“只读目录清单同步”已经稳定之后，而不是一次性把协议做复杂。

## 13. References

- `docs/current/mail_protocol.md`
- `docs/current/android_reply_method_rules.md`
- `docs/current/session_scheduler_status.md`
- `mail_runner/parser.py`
- `mail_runner/app.py`
