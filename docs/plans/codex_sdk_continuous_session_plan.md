# Codex TypeScript SDK 连续会话实施方案（P1）

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 本文是 [coding_backlog.md](./coding_backlog.md) 中 P1「Codex SDK 连续会话接入」的详细实施方案，不改变当前现网行为。
> 与之配套的实测边界见 [codex_sdk_capability_probe.md](./codex_sdk_capability_probe.md)。

## 0. 状态

- 日期：2026-03-18
- 对应主计划：`docs/plans/coding_backlog.md` 的 P1
- 当前目标：把 P1 从“方向性计划”收口为“可执行实施设计”
- 当前主线：TypeScript `@openai/codex-sdk` sidecar
- 当前备线：Python 直连 `codex mcp-server`

## 1. 问题定义

当前 Codex 接入仍然是 CLI 薄封装：

- `mail_runner/adapters/codex_adapter.py` 通过 `codex exec` / `codex exec resume` 工作
- continuation / `/resume` 依赖 `backend_session_id`
- `TaskSnapshot.run_mode == "resume"` 时，恢复路径仍然走 native CLI resume

这条路径当前能工作，但有一个核心问题：

- native `resume` 一旦丢失上下文，应用层没有真正的连续会话真相层兜底

P1 的目标不是推翻现有 runner，而是在不打断当前 CLI 工作流的前提下，为 Codex 增加一条真正连续的 SDK 会话路径。

当前已确认的选型结论是：

- 主线采用 TypeScript `@openai/codex-sdk`
- 通过最小 Node sidecar 暴露给 Python 主程序
- 不把 Python Agents SDK 作为第一轮主实现路径

## 2. 本轮目标

P1 只解决三件事：

1. 为 Codex 新增 SDK 连续会话通道。
2. 让同一 thread 的后续 turn 可以稳定回到同一个 SDK 会话。
3. 保留现有 CLI 通道作为 fallback，不强制切换已有 thread。

P1 不解决：

- `active <= 4` 的工作集控制
- `/end`、`active/ended` 生命周期轴
- stuck/orphaned 健康判断
- `[DONE]` 邮件保留语义改造
- OpenCode 的 SDK 化或 transport 拉齐

这些内容继续留在 `coding_backlog.md` 的 P2-P6。

## 2.1 为什么主线选 TypeScript SDK sidecar

当前这个选择已经固定，原因如下：

1. `@openai/codex-sdk` 是官方公开、文档完整、且已在本机实测跑通的路径。
2. 它直接复用本机 Codex CLI，不要求额外 `OPENAI_API_KEY`，符合“依赖 Pro 用户本地 Codex 能力，不额外购买 API token”的约束。
3. 它已经公开支持：
   - `startThread()`
   - `resumeThread(threadId)`
   - `run()`
   - `runStreamed()`
   - `outputSchema`
4. Python Agents SDK 路线更适合“外层再套一个 OpenAI agent orchestrator”的场景，不适合作为当前仓库的最低风险主线。

## 2.2 为什么 Python 直连 MCP 只保留为备选

Python 侧通过 `codex mcp-server` 也能工作，但当前能力面更弱：

- `codex` / `codex-reply` 两个工具可以跑通
- 不需要额外 API key
- 但没有 TypeScript SDK 的 `outputSchema`
- `codex-reply` 当前只接 `threadId + prompt`
- Python 通用 MCP 客户端会对 `codex/event` 自定义通知产生兼容性告警

因此：

- 它适合作为备选或参考实现
- 不适合作为 P1 主实现

## 3. 当前实现基线

当前代码里的几个关键约束如下：

- `Dispatcher` 只按 `backend` 选路，不区分同一 backend 的不同 transport。
- `WorkerAdapter` 当前只有 `run(task, run_dir)` 和 `kill(task_id)` 两个接口。
- `ThreadState`、`SessionState`、`TaskSnapshot`、`RunResult` 当前都只持久化通用的 `backend_session_id` / `backend_session_resumable`。
- `runner._finalize_thread_state()` 会把 `RunResult.backend_session_id` 回写到 thread / session。
- `task_compiler` 的 continuation / answer 路径会把 `thread_state.backend_session_id` 重新放回 `TaskSnapshot.backend_session_id`。

这说明：

- 仓库已经有“连续会话 id 持久化”的基本骨架
- 但它还不足以表达 “Codex CLI 会话” 与 “Codex SDK 会话” 的区别

## 4. 设计原则

P1 必须遵守以下原则：

1. additive path 优先，不做 destructive replacement。
2. 现有 `CodexAdapter` 保留，可继续服务当前线程。
3. 不要求迁移已有 in-flight thread。
4. 不引入按标题自动复用 session 的隐式猜测。
5. 同一 thread 一旦选定 transport，后续 continuation 默认沿用，不在中途自动漂移。
6. 如 SDK 侧关键能力缺口未确认，不能直接让 live mail flow 强依赖它。
7. TypeScript SDK 与 Python 主程序之间的桥接必须尽量薄，不在 P1 同时引入复杂本地服务编排。

## 5. 关键设计决策

### 5.1 `backend` 继续保持 `codex`

P1 不新增 `backend=codex_sdk` 这种新 backend 名。

理由：

- 当前系统的产品语义是“后端类型”，不是“接入方式”
- `codex` 仍然是同一个产品后端，CLI / SDK 只是 transport 差异

因此需要新增的是 transport 维度，而不是 backend 维度。

### 5.2 新增 `backend_transport`

P1 建议在下列模型中新增可选字段 `backend_transport`：

- `TaskSnapshot`
- `ThreadState`
- `SessionState`
- `RunResult`

首轮合法值建议：

- `cli`
- `sdk`

默认值策略：

- 旧数据缺失时按 `cli` 解释
- 新建 Codex thread 时，按配置决定默认 transport
- follow-up / `/resume` / question answer 默认继承当前 thread 的 transport

### 5.3 继续复用 `backend_session_id`

P1 不建议一上来重命名所有会话字段。

建议语义统一为：

- `backend_session_id`：当前 transport 的“连续会话主标识”

具体到 Codex：

- CLI 路径：继续存 native CLI session id
- SDK 路径：存 SDK thread/session 的连续标识

如 SDK 需要额外的 run id / event cursor，首轮应作为新增可选字段处理，而不是替换 `backend_session_id`。

建议新增但非强制的辅助字段：

- `backend_run_id`
- `backend_last_event_id`

是否落库，以 SDK 实际公开能力为准。

### 5.4 引入 Codex transport 路由层，而不是改写 Dispatcher 语义

当前 `Dispatcher` 只按 `backend` 选路，这个边界可以保留。

P1 建议：

- 保留 `Dispatcher(backend -> adapter)` 的语义不变
- 在 Codex 槽位里放一个新的 `CodexRoutingAdapter`
- `CodexRoutingAdapter` 内部根据 `task.backend_transport` 决定调用：
  - `CodexAdapter`（现有 CLI 实现）
  - `CodexSdkAdapter`（新增 SDK 实现）

这样做的好处是：

- 改动范围局部
- OpenCode 完全不受影响
- 现有 `Dispatcher`、`Runner`、`App` 的高层语义不需要重写

### 5.5 默认切换策略采用“新线程按配置，老线程按持久化”

P1 不做自动迁移。

建议规则：

- 老 thread：如果没有 `backend_transport` 字段，按 `cli` 处理
- 新 thread：如果 `backend == codex`，按配置里的默认 transport 选择 `cli` 或 `sdk`
- continuation：始终继承 thread 当前记录的 `backend_transport`

这保证：

- 当前正在用的线程不会突然切 transport
- 可渐进试点 SDK 路径

### 5.6 TypeScript SDK 以 sidecar 形态接入

P1 不建议让 Python 直接嵌 Node 运行时细节到业务代码里。

建议结构：

- Python 主程序继续保留为仓库主运行时
- 新增一个最小 Node sidecar 进程
- sidecar 内部唯一职责是调用 `@openai/codex-sdk`
- Python 通过本地子进程 + JSON stdin/stdout 或等价的本地 IPC 调 sidecar

这样做的理由：

- 与现有 Python runner 改动面最小
- 可以把 TypeScript 依赖和 Python 依赖隔离
- 出问题时更容易回退到现有 CLI adapter

## 6. 配置设计

P1 建议新增一项配置：

- `codex_transport_default: cli | sdk`

并建议为 sidecar 增加最小配置项：

- `codex_sdk_sidecar_command`
- `codex_sdk_sidecar_workdir`

首轮默认值必须是：

- `cli`

原因：

- 不打断现有工作流
- 先让 SDK 变成可选开关

首轮不建议加入复杂的按 workspace / profile 细粒度 transport 路由规则。

## 7. 模型与持久化演进

### 7.1 需要更新的模型

至少需要改这些 dataclass：

- `TaskSnapshot`
- `RunResult`
- `ThreadState`
- `SessionState`

必要字段：

- `backend_transport: str = "cli"`

可选字段：

- `backend_run_id: str | None = None`

### 7.2 向后兼容要求

旧 JSON 状态必须继续能读。

因此字段设计必须满足：

- 有默认值
- 缺失时不触发 model validation 错误
- 旧 thread 不需要迁移脚本也能继续跑

### 7.3 thread/session 同步要求

当前 `thread_store` 已经负责 thread -> session / workspace 索引同步。

P1 要求：

- `backend_transport` 必须和 `backend_session_id` 一样，在 thread / session 层保持一致
- `RunResult` 回写 thread state 时也要回写 transport

## 8. Adapter 分层

### 8.1 保留现有 `CodexAdapter`

当前 `CodexAdapter` 继续代表：

- Codex CLI transport

为减少改动，首轮不强制重命名成 `CodexCliAdapter`。

### 8.2 新增 `CodexSdkAdapter`

`CodexSdkAdapter` 应实现 `WorkerAdapter`，至少具备：

- 调用 Node sidecar 创建 SDK 连续会话并执行首轮任务
- 基于已持久化的 SDK 会话 id 继续后续 turn
- 产出与现有 `RunResult` 兼容的结果
- best-effort kill / cancel

首轮这里的 `CodexSdkAdapter` 是 Python 侧适配器，不是 TypeScript SDK 本身。

### 8.3 新增 `CodexRoutingAdapter`

职责：

- 对外仍实现 `WorkerAdapter`
- 内部根据 `task.backend_transport` 分发到 CLI 或 SDK
- 对缺失 transport 的旧任务按 `cli` 解释

### 8.4 SDK adapter 的最小职责

P1 首轮不要求 SDK adapter 暴露完整事件流。

最小闭环即可：

- new run
- continuation run
- 结果归并为 `RunResult`
- session/thread id 持久化

事件级 observability 留给 P3 增量展开。

### 8.5 sidecar 的最小协议

首轮建议只定义少量本地调用动作：

- `start`
- `reply`
- `cancel`（best-effort）

建议每个动作都返回统一 JSON：

- `threadId`
- `finalResponse`
- `usage`
- `items` 或压缩后的事件摘要
- `error`

P1 不要求 sidecar 暴露完整通用 RPC。

## 9. 任务编译与恢复规则

### 9.1 新任务

首封新任务创建 `TaskSnapshot` 时：

- 若 backend 不是 `codex`，不受影响
- 若 backend 是 `codex`，给 snapshot 写入默认 `backend_transport`

### 9.2 普通 reply / `/resume`

当前 continuation 路径会把 `thread_state.backend_session_id` 放回 snapshot。

P1 要求同时继承：

- `backend_transport`
- `backend_session_id`

这样 `CodexRoutingAdapter` 才能稳定回到正确 transport。

### 9.3 failed fallback recovery

当前系统对 failed thread 已有 fresh recovery run 兜底。

P1 不改变这条协议语义，只要求：

- 若 thread 当前 transport 是 `sdk` 且 SDK 会话仍可继续，则优先继续 SDK 会话
- 若 SDK 会话不可继续，则沿用现有 recovery 语义，启动 fresh run
- fresh run 的 transport 继承当前 thread，除非显式配置或实现上确认必须回退 CLI

### 9.4 killed 风险恢复

当前 killed 线程允许保留 resumable backend context。

P1 的原则是：

- 不降低现有 `/kill -> /resume` 语义
- 如果 SDK 无法提供等价 cancel / resume 能力，则 SDK transport 不能直接成为 live 默认

这是一项上线前检查项，不是可以事后补的细节。

## 10. 结果映射要求

`CodexSdkAdapter` 最终仍要产出统一的 `RunResult`。

至少要映射这些字段：

- `status`
- `summary`
- `backend`
- `backend_transport`
- `backend_session_id`
- `backend_session_resumable`
- `stdout_file` / `stderr_file` 或与当前 reporter 兼容的替代落盘
- `question_*` 字段（如 SDK 侧存在等待用户输入语义）

如果 SDK 侧暂时拿不到某些 CLI 才有的文本输出：

- 可以缺省为更保守的落盘内容
- 但不能破坏当前 reporter / artifact / status mail 的读法

## 11. 权限、模型与工作目录映射

P1 不能只解决“连续”，还要保证当前执行语义不回退。

因此 `CodexSdkAdapter` / sidecar 组合需要复用现有几类输入：

- `profile -> model` 映射
- `permission` 语义
- `repo_path` / `workdir`
- `turn_text`
- `attachments`

这里的要求是“语义对齐”，不要求 SDK 参数名与 CLI 完全一样。

如果某一项在 SDK 中暂时没有等价表达：

- 文档里先标为 blocker
- 实现上不要静默降级成不同语义

## 12. 观察与排障字段

虽然 P3 才做最小 PC 可视化，但 P1 需要先把基础字段埋好。

建议至少持久化并能在 `observe` 里读取：

- `backend_transport`
- `backend_session_id`
- `backend_run_id`（如果有）
- `updated_at`
- `last_summary`

P1 不要求把它们全部展示成完整 UI，但字段本身应该先进入状态层。

## 13. 测试矩阵

P1 需要新增自动化覆盖，至少包括以下几组。

### 13.1 模型与持久化

- 旧状态文件缺失 `backend_transport` 时仍能正常加载
- thread -> session 同步会保留 `backend_transport`
- `RunResult` 回写后，thread state 中的 transport / session id 一致

### 13.2 选路逻辑

- `backend=codex, backend_transport=cli` 时走现有 CLI adapter
- `backend=codex, backend_transport=sdk` 时走新 SDK adapter
- 旧数据无 transport 时默认走 CLI

### 13.2.1 sidecar 协议

- Python 侧能成功拉起 sidecar
- sidecar 能返回可解析 JSON
- sidecar 失败时 Python 侧能把错误折叠成稳定的 `RunResult` 失败态

### 13.3 连续会话闭环

- 新建 codex sdk thread
- 第一次 follow-up 继续同一 SDK session
- 第二次 follow-up 继续同一 SDK session
- 最终完成并进入 `DONE`

### 13.4 失败与回退

- SDK session 不可恢复时，failed thread 仍可走 fresh recovery run
- CLI fallback 路径不受 SDK 代码引入影响

### 13.5 kill / resume

- SDK transport 下 `/kill` 不会破坏 thread 状态一致性
- 如 SDK 支持 resume，`/resume` 能回到同一连续会话
- 如 SDK 不支持 resume，系统行为必须明确且已文档化

## 14. 实施顺序

建议按以下顺序编码：

1. 补文档，冻结 P1 设计口径。
2. 固定 sidecar 方案与本地 JSON 协议。
3. 给模型和持久化层加 `backend_transport` 等字段，保持向后兼容。
4. 实现 `CodexRoutingAdapter`，但先继续全部走 CLI。
5. 实现最小 Node sidecar，先只支持 `start` / `reply`。
6. 实现 Python 侧 `CodexSdkAdapter` 与最小 fake/mocked tests。
7. 把 Codex 新线程的 transport 选择接到配置。
8. 打通“新建 -> 两次 continuation -> DONE”的集成测试。
9. 最后再考虑把 SDK 作为可选默认值开放。

## 15. 前置检查项

P1 开始编码前，必须先确认以下外部事实：

1. `@openai/codex-sdk` 的版本与本机 `codex` CLI 版本如何一起固定。
2. sidecar 协议里哪些字段是必须保留的最小集合。
3. SDK 是否支持 run cancel / interrupt，以及这些能力不能被误当成可靠 kill。
4. SDK 是否能表达当前仓库已有的权限语义。
5. SDK 是否能稳定绑定 repo/workdir。

无需再把“是否存在单独公开的 Python Codex SDK 包”作为 P1 前置条件。

如果以上任一项结论不清楚：

- 可以先写适配层骨架
- 但不应把 SDK 路径设为 live 默认

## 16. Done When

P1 可以视为完成，当且仅当：

- Codex SDK 路径已经可选启用
- TypeScript sidecar 已经成为 Python 主程序可调用的稳定本地执行通道
- 新建的 Codex SDK thread 可以连续至少两轮 follow-up
- 同一 thread 的 continuation 不再依赖 `codex exec resume`
- 旧 CLI thread 不受影响
- 自动化测试覆盖 transport 选路、状态持久化和连续会话 happy path
- 文档明确记录 SDK 仍是 additive path，而不是对 CLI 的破坏式替换
