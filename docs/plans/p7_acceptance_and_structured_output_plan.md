# P7 Acceptance And Structured Output Plan

> 文档层级：Layer 2（当前仓库的实现计划）
>
> Scope: `mail_based_task_manager`
>
> Date: 2026-03-18
>
> Status: completed
>
> Notes: Both `P7A` and `P7B` are complete. This plan now serves as the closed implementation record for `P7`.

## Goal

把当前 backlog 里原本模糊的 `P7` 收口成两条低风险、高收益的主线：

1. `P7A`: 固定 real-mailbox acceptance
2. `P7B`: CLI structured output parsing

这两个子项都应在**不改变 Android 当前 reply protocol**、**不引入新的 session targeting 协议**的前提下推进。

## Why These Two First

当前 Layer 1 文档和状态页里最明确的未完成项有两类：

- 固定 real-mailbox acceptance 仍缺真实 backend 的 `[QUESTION] -> ANSWER -> DONE` 和正常 mailbox loop 下的 `KILL`
- `changed_files` / `tests_passed` 需要从真实 CLI / SDK 回复里拿到稳定的结构化结果，而不是长期依赖空值和启发式 summary

相比之下，下面这些工作虽然有价值，但不应插入本轮：

- 显式 session targeting UX
- 按标题自动复用 session
- artifact Markdown / renderer 深度收口
- 其余 adapter 的长期统一

原因是它们要么会触及 Android/邮件协议面，要么会把 `P7` 扩成另一条大主线。

## Fixed Decisions

本轮先固定以下决策：

- `P7` 只做 `P7A + P7B`
- 不新增 Android 端必须配合的新协议
- 不新增显式 session switch 协议
- 不启用按 `workspace + title` 自动复用 session
- 允许 structured output parsing 先只覆盖当前已接入的真实 CLI / SDK 结果面，不要求一步统一所有 adapter
- structured output parsing 必须保持启发式 summary fallback

## P7A: Fixed Real-Mailbox Acceptance

### Product Target

把“曾经联调过”升级为“固定验收项”，使仓库对真实邮箱 + 真实 backend 的关键交互闭环有稳定证据。

### Scope

本轮至少固定这两条 acceptance：

1. `[QUESTION] -> ANSWER -> DONE`
2. 正常 mailbox loop 下的真实 backend `KILL`

第一条覆盖：

- 真实收信
- 状态邮件投递
- 问题邮件回复
- answer 解析
- continuation / resume
- 最终 receipt 投递

第二条覆盖：

- 正常轮询路径下的 kill 触发
- 正确终止当前 run
- 最终 `[KILLED]` 回执与状态落盘

### Non-Goals

- 不把所有历史 live smoke 都升级成固定 acceptance
- 不解决单邮箱自回信的 provider 差异
- 不为 acceptance 先改 Android 客户端策略

### Implementation Shape

建议按“脚本 + 验收口径 + 文档引用”落地：

1. 先盘点现有 live smoke 脚本和 runtime 依赖
2. 补齐可重复执行的真实邮箱 acceptance 脚本或脚本参数
3. 固定输出目录、成功判定和失败证据
4. 把通过标准写回 `state.md` / `README.md` / `docs/current/*`

### Done When

- 仓库中存在可重复执行的真实邮箱 acceptance 路径
- `[QUESTION] -> ANSWER -> DONE` 被记录为固定 acceptance
- 真实 backend `KILL` 被记录为固定 acceptance
- 成功与失败都能留下明确工件路径，便于复盘

### Execution Evidence

- `2026-03-18`: `scripts/live_smoke_mail_question_answer.py` -> `_tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74\result.json`
- `2026-03-18`: `scripts/live_smoke_mail_kill.py` -> `_tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5\result.json`

## P7B: CLI Structured Output Parsing

### Product Target

让 `RunResult` 中的 `changed_files`、`tests_passed`、错误类型等字段尽量来自真实 CLI 结构化结果，而不是长期停留在空值 + 启发式推断。

### Scope

第一轮最小目标：

- adapter 层补 CLI structured output parsing
- 在不破坏现有结果落盘格式的前提下，填充：
  - `changed_files`
  - `tests_passed`
  - 可选的 `error_type`
  - 可选的 `error_message`
- 无结构化输出时继续回退到当前启发式路径

### Non-Goals

- 不在本轮正式化完整 `RunRequestLite` / `RunResultEnvelope`
- 不在本轮完成 `tools.list` / `tools.describe` / `task-run-packet` bridge
- 不强制所有 adapter 一次性对齐到同一 typed envelope

### Implementation Shape

建议顺序：

1. 盘点当前 `Codex` / `OpenCode` / `SDK sidecar` 哪些路径能拿到结构化结果
2. 在 adapter 侧补最小解析层，统一投影到现有 `RunResult`
3. 保留当前 summary/error fallback
4. 在 reporter / status mail / result.json 上验证字段传播

### Done When

- 至少一个真实 backend path 能稳定产出非空 `changed_files` 或明确的 `tests_passed`
- `RunResult` 现有字段保持兼容
- 没有 structured output 的路径仍能继续工作
- 相关测试覆盖“有结构化结果”和“回退到旧路径”两类场景

### Execution Evidence

- `2026-03-18`: `mail_runner.run_result_capsule` landed as the shared parser/stripper for CLI and SDK paths
- `2026-03-18`: `tests/test_run_result_capsule.py`, `tests/test_cli_adapters.py`, `tests/test_codex_sdk_adapter.py`, and `tests/test_reporter.py` cover parsing, projection, and user-visible stripping
- `2026-03-18`: full regression `.\.venv\Scripts\python.exe -m pytest` -> `231 passed`
- `2026-03-18`: live mailbox OpenCode smoke `_tmp_live_mail_structured_result_smoke\p7b-structured-20260318_141408-44bfca\result.json` verified `changed_files=["docs/current/mail_protocol.md","README.md"]`, `tests_passed=false`, and user-visible `[DONE]` reply stripping on the real host after the controlled restart

## Recommended Order

推荐顺序固定为：

1. `P7A` 已完成
2. `P7B` 已完成
3. `P7` 现已收口，后续工作需要进入新的优先级决策

原因：

- `P7A` 已经补齐运行时真验收证据
- `P7B` 已经把结果真相层补齐到兼容可用的第一轮
- 统一重启可以减少 live runtime 被打断的次数

## Test Gates

本轮至少要求：

### P7A

- acceptance 脚本或固定执行步骤可重复运行
- 真实邮箱验证结果能留下工件目录
- 文档中能引用成功样例
- `2026-03-18` 的两条 live acceptance 已经满足上述门槛

### P7B

- adapter 单测覆盖 structured output 解析
- app / reporter / runner 级别至少有一条字段传播测试
- 全量回归 `.\.venv\Scripts\python.exe -m pytest`
- `2026-03-18`：以上门槛已满足，回归结果为 `231 passed`
- `2026-03-18`：controlled live-host restart + `_tmp_live_mail_structured_result_smoke\p7b-structured-20260318_141408-44bfca\result.json` 补齐了真实 mailbox/backend 的结构化结果证据

## Explicitly Deferred

以下事项明确延后，不并入 `P7`：

- 显式 session targeting UX
- Android 侧新命令协议
- 按标题自动复用旧 session
- artifact Markdown / renderer 收口
- adapter 全量统一重构
