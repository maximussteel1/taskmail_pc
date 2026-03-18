# Codex SDK / MCP 交互能力探测（2026-03-18）

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 本文记录 P1 开始前对 Codex TypeScript SDK 与 Python 直连 MCP 路径的交互边界实测结果，用于约束后续实现设计。

## 0. 探测目标

本轮不做正式接入，只回答后续协议与 runner 会依赖的几个问题：

1. SDK 是否真的能拉起 Codex，并保持连续会话
2. 输入大小大致能到什么量级
3. 是否支持中断
4. 中断后是否能结束底层执行
5. 权限选择是否能交互
6. 官方 CLI 的 `/` 命令是否有 SDK 对应面
7. 问题 / 选项交互是否有原生协议
8. Python 路径是否适合成为 P1 主线

## 1. 探测环境

- 日期：2026-03-18
- 工作目录：`E:\projects\mail_based_task_manager`
- 本机 `codex --version`：`codex-cli 0.115.0`
- 探测包：`@openai/codex-sdk@0.115.0`
- 依赖 CLI 包：`@openai/codex@0.115.0`
- Node：`v24.13.0`
- Python 探测环境：`openai-agents==0.12.3`、`mcp==1.26.0`

官方参考：

- <https://developers.openai.com/codex/sdk>
- <https://developers.openai.com/codex/app-server>
- <https://developers.openai.com/codex/changelog>

## 2. 已确认能力

### 2.1 SDK 可以直接拉起 Codex

本机可以直接：

- `npm install @openai/codex-sdk`
- `new Codex()`
- `startThread()`
- `thread.run(...)`
- `resumeThread(threadId)`

最小两轮连续会话实测成功，thread id 可复用。

### 2.2 SDK 当前本质上是 CLI 包装层

从本地安装包 `README.md` 与 `dist/index.js` 可确认：

- TypeScript SDK 通过本地 `codex` CLI 工作
- 通过 stdin / stdout 交换 JSONL 事件
- 不是一个独立于 CLI 的新执行内核

这意味着：

- SDK 的连续会话能力可用
- 但其进程、中断、审批等很多行为仍继承 CLI / exec 模式的约束

### 2.3 连续会话可用

实测：

- 第一轮 `thread.run("Reply with exactly: SDK_TURN1")`
- 持久化 `thread.id`
- `resumeThread(threadId).run("Reply with exactly: SDK_TURN2")`

结果：

- `turn1 = SDK_TURN1`
- `turn2 = SDK_TURN2`

这说明 P1 所需的“真正连续 thread/session”在 SDK 路径上是成立的。

### 2.4 输入至少可稳定承载 32768 个字符

实测向 `thread.run()` 发送了：

- `32768` 个 filler 字符
- 返回成功
- usage 中记录 `input_tokens = 12676`

结论：

- SDK 本身没有暴露出单独的“小输入上限”
- 当前至少已确认可以稳定承载约 `32KB` 级别的纯文本输入

注意：

- 这不是最终上限
- 更像是“已验证下限”
- 真正上限更可能受模型上下文或底层 CLI / API 限制影响，而不是 SDK 自己的字符串接口

### 2.5 结构化输出可用，可承载应用层问题协议

实测使用 `outputSchema` 要求模型返回：

- `kind`
- `question`
- `choices`

返回成功，得到结构化 JSON：

```json
{"kind":"question","question":"Should I continue with the SDK or the CLI?","choices":["SDK","CLI"]}
```

结论：

- SDK 没有原生“问题协议”
- 但可以通过 `outputSchema` 做稳定的应用层问题 / 选项输出

### 2.6 Python 可直接连接 `codex mcp-server`

实测在 Python 中通过 MCP stdio client 可以：

- 启动 `codex mcp-server`
- `list_tools()`
- 调 `codex`
- 调 `codex-reply`

并且不需要额外 `OPENAI_API_KEY`。

当前确认暴露的工具只有两个：

- `codex`
- `codex-reply`

其中：

- `codex` 支持 `prompt`、`cwd`、`approval-policy`、`sandbox`、`model`、`profile`、`config`
- `codex-reply` 当前只支持 `threadId` / `conversationId` + `prompt`

### 2.7 Python MCP 路径的连续会话也可用

实测：

- `call_tool("codex", ...)`
- 读取返回的 `structuredContent.threadId`
- 再调用 `call_tool("codex-reply", {"threadId": ..., "prompt": ...})`

结果：

- 可以稳定回到同一个 thread
- 返回结构中包含：
  - `threadId`
  - `content`

## 3. 已确认限制

### 3.1 reasoning effort 不能随便写

本机默认模型不支持 `minimal`。

实测报错：

- `Unsupported value: 'minimal'`
- 支持值为 `none / low / medium / high / xhigh`

结论：

- P1 不能把 `modelReasoningEffort` 写死成 `minimal`
- 需要允许配置映射或保持跟随当前 Codex 配置

### 3.2 `web_search` 与 reasoning effort 组合有兼容性约束

第一次最小测试时，如果保留当前默认 `web_search`，同时使用过低 reasoning effort，会直接收到 `400 invalid_request_error`。

结论：

- P1 接入时不能只传 reasoning effort，还要一起考虑 `webSearchMode`
- 这些配置必须走显式映射，不能隐式继承到不可预测状态

### 3.3 没有显式 kill / close API

从 SDK 类型面可见：

- `Codex` 只有 `startThread()` / `resumeThread()`
- `Thread` 只有 `run()` / `runStreamed()`
- 没有 `kill()`、`cancel()`、`close()` 之类的显式方法

唯一明确的中断面是：

- `TurnOptions.signal?: AbortSignal`

### 3.4 Python 通用 MCP 客户端当前不能干净消费 `codex/event`

实测 Python 直连 `codex mcp-server` 时：

- 主调用本身成功
- 但 `mcp` Python SDK 会对 `codex/event` 自定义通知产生验证告警

这说明：

- Python 直连 MCP 不是不可用
- 但当前兼容性体验不如 TypeScript SDK 平滑

## 4. 中断与进程结束的实测结论

### 4.1 turn 可以被中断

实测：

- 用 `runStreamed()` 发起一个会执行长命令的 turn
- 看到 `command_execution` 开始后
- 通过 `AbortController.abort()` 中断

结果：

- SDK 返回错误：`The operation was aborted`

这说明：

- turn 级中断是成立的

### 4.2 但中断不等于底层命令被可靠终止

同一次实验里，命令本应在 12 秒后写入一个 marker 文件。

结果：

- turn 已经 abort
- 但 marker 文件最终仍然被写出

这说明：

- SDK 的 `AbortSignal` 至少目前不能保证“命令已被彻底杀死”
- 更准确地说，它更像是“中断当前 SDK/CLI 会话”，而不是“可靠清理整个子进程树”

对本仓库的意义非常大：

- P1 如果引入 SDK transport，不能直接把 `AbortSignal` 当成 `kill` 语义
- `/kill` 对 SDK transport 需要单独设计
- 否则会出现“线程以为停了，但底层命令其实还在跑”的风险

### 4.3 Python 直连 MCP 的取消同样不能直接当成 kill

Python 侧补充实测了两种情况：

- 早取消：`asyncio.CancelledError`，marker 未出现
- 晚取消：被用户中断后的现场确认里，`py_abort_marker_late.txt` 最终已写出

结论：

- Python 直连 MCP 这条路径同样不能把“取消 call”理解成“可靠终止底层命令”
- 这进一步强化了：P1 里的 `/kill` 不能建立在 SDK/MCP 自带 cancel 幻觉上

## 5. 权限与审批交互的实测结论

### 5.1 权限模式可以配置，但不是交互式审批面

从类型面可确认 `ThreadOptions` 支持：

- `sandboxMode`
- `approvalPolicy`

实测使用：

- `sandboxMode: "read-only"`
- `approvalPolicy: "on-request"`

要求模型写文件。

结果：

- 文件没有写出
- turn 没有出现任何“approval requested”之类的事件
- 最终回复明确表示：当前 session 是只读，approval escalation disabled，所以写入被阻止

结论：

- SDK 虽然接受 `approvalPolicy` 参数
- 但当前这条非交互式 exec/SDK 路径上，不能把它理解成“可以像 IDE/TUI 那样弹审批并等待用户选择”
- 至少按当前实测，P1 不能依赖 SDK 提供交互式权限审批

## 6. `/` 命令与问题交互的结论

### 6.1 官方 CLI 的 `/` 命令没有 SDK 原生 API 面

从 SDK 类型和实现可直接看出：

- `Thread.run()` 的输入只有 `string` 或 `UserInput[]`
- SDK 只是把输入文本通过 stdin 发给 `codex exec`
- 没有单独的 slash-command channel

结论：

- 官方 CLI/TUI 里的 `/` 命令，不应假设在 SDK 中有同等原生接口
- 本仓库自己的 `/new`、`/resume`、`/pause`、`/kill` 仍应保持为应用层协议

### 6.2 没有原生的“问题 / 选项选择”事件协议

当前事件类型只有：

- `thread.started`
- `turn.started`
- `turn.completed`
- `turn.failed`
- `item.started`
- `item.updated`
- `item.completed`
- `error`

item 类型包括：

- `agent_message`
- `reasoning`
- `command_execution`
- `file_change`
- `mcp_tool_call`
- `web_search`
- `todo_list`
- `error`

没有：

- `question`
- `choice_prompt`
- `approval_request`
- `awaiting_user_input`

结论：

- 如果后续要保留 `[QUESTION]` / 选项题语义，仍然要由本仓库应用层负责定义和投影
- SDK 最多提供更稳定的结构化输出与连续会话基础

## 7. 对 P1 的直接影响

这轮探测后，P1 的实现边界可以明确成：

1. SDK 非常适合解决“连续会话”问题。
2. SDK 不适合直接承担“进程可靠终止”语义。
3. SDK 不能被当作“交互式审批 UI”。
4. 本仓库现有 `/...` 命令和 `[QUESTION]` 协议仍然应该保留在应用层。
5. `backend_transport = sdk` 的 thread 需要单独定义 kill 风险和回退策略。
6. P1 主线更适合选 TypeScript SDK，而不是 Python 直连 MCP 或 Python Agents SDK。

## 7.1 为什么不选 Python Agents SDK 做主线

当前的结论不是“Python 不能集成 Codex”，而是：

- Python Agents SDK 更适合外层再套一个 OpenAI agent orchestrator
- 这条路更容易引入额外 API token 成本
- 对当前仓库“继续复用本机 Codex CLI / Pro 能力”的目标不够直接

因此：

- Python Agents SDK 不作为 P1 主线
- TypeScript `@openai/codex-sdk` sidecar 才是当前固定方向
- Python 直连 `codex mcp-server` 只保留为备选或参考实现

## 8. 尚未探清的问题

以下问题仍未完全确认：

- SDK thread 在重新 resume 时，是否安全支持切换 `sandboxMode` / `approvalPolicy`
- 输入上限的更高边界
- app-server 路径是否能提供比当前 TS SDK 更强的 cancel / approval 能力
- sidecar 与 Python 主程序之间的最小 JSON 协议如何定得既薄又稳定

## 9. 建议下一步

在正式写 P1 代码前，建议先按这个顺序继续探：

1. TypeScript sidecar 的本地 JSON 协议
2. SDK transport 下 `/kill` 的可接受实现语义
3. SDK transport 下 question / approval 的应用层投影策略
4. `backend_transport`、`backend_session_id`、`backend_run_id` 的最终落库设计
