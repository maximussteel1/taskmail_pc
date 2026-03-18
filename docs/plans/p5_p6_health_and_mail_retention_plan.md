# P5/P6 Health And Mail Retention Plan

> 文档层级：Layer 2（当前仓库的实现计划）
>
> Scope: `mail_based_task_manager`
>
> Date: 2026-03-18
>
> Implementation Status: landed in code on 2026-03-18; unified live smoke remains deferred until the next controlled host restart.

## Goal

用一轮连续实现把 backlog 中的 P5 和 P6 收口：

1. 先让用户能判断一个线程只是“久未更新”，还是“疑似卡住/已孤儿化”
2. 再把 thread 真实状态与 live mailbox 的保留语义拆开，避免 `[DONE]` / `[FAILED]` / `[KILLED]` 被错误清掉

这个计划默认不打断当前 live host；代码完成后与 P6 一起做统一重启和烟测。

## Fixed Decisions

以下口径在本轮实现中固定，不再边做边改：

- P5 先于 P6 落地
- 第一轮健康阈值固定为 `300s`
- 第一轮不增加新的用户命令面
- 第一轮不引入数据库或额外后台 watchdog 进程
- 第一轮以 additive 方式演进状态模型，不重写现有 `status` / `lifecycle`
- 第一轮不改变 Android 侧 reply protocol

## P5 Scope

### Product Target

用户至少应能区分：

- `normal`
- `stale`
- `suspected_stuck`
- `orphaned`

并且能看到最小“最近进展”真相层。

### First-Round Data Model

第一轮只新增最小持久化字段：

- `last_progress_at`

落点：

- `ThreadState.last_progress_at`
- `SessionState.last_progress_at`

第一轮不把 `health_status` 直接持久化到 thread/session state，而是统一由共享 helper 在观察面和状态展示时派生。

可选补充字段只有在实现中确实需要时才增加：

- `health_reason`

### Progress Truth Sources

`last_progress_at` 的更新顺序按下面口径实现：

1. thread/session 进入 `accepted`
2. thread/session 进入 `running`
3. queued follow-up 被提升为当前任务
4. 线程进入 `awaiting_user_input`
5. 线程进入 `paused`
6. 线程执行完成并落最终结果
7. `/resume`、`/end` 等显式控制面改变线程可继续性

对 `codex + sdk` 运行中的 thread，观察面允许用 `stream.events.jsonl` 的最新事件时间覆盖持久化的 `last_progress_at`，以减少“明明还在流式输出却被误判 stale/stuck”的概率。

### Health Derivation Rules

第一轮统一使用固定阈值：

- `HEALTH_STALE_AFTER_SECONDS = 300`

派生规则按强弱顺序判定：

1. `orphaned`
   - thread/session 状态仍显示 `accepted` 或 `running`
   - 但 host 已不可用，或当前 runtime 明显没有能力继续推进这条工作
2. `suspected_stuck`
   - thread/session 应该正在推进
   - host 仍存活
   - 距离最近进展时间超过 `300s`
3. `stale`
   - 非 terminal 的 thread/session 没有新进展超过 `300s`
   - 但不满足 `orphaned` / `suspected_stuck`
4. `normal`
   - 其余情况

第一轮的“应该正在推进”定义收口为：

- `accepted`
- `running`

`paused`、`awaiting_user_input`、`done`、`failed`、`killed`、`ended` 默认不进入 `suspected_stuck`，最多进入 `stale`。

### P5 Surfaces

第一轮至少更新这些可见面：

- `mail_runner.observe status`
- `mail_runner.observe list-running`
- `mail_runner.observe show-thread`
- `mail_runner.observe show-thread-live`

展示内容至少包括：

- `health`
- `last_progress_at`
- `idle_for` 或等价 elapsed 文本
- `reason`（若有）

状态邮件第一轮只需要在 `[STATUS]` / `[PAUSED]` / `[QUESTION]` 等非终态用户可操作视图里露出健康信息；不要求重做所有 receipt 模板。

### P5 Test Gates

至少覆盖：

- host 存活且运行线程 `>300s` 无进展 => `suspected_stuck`
- host 不存活但 thread 仍是 `accepted/running` => `orphaned`
- `awaiting_user_input` 或 `paused` 超过 `300s` => `stale` 而不是 `suspected_stuck`
- `codex + sdk` 有新 stream 事件时不会被误判为 stuck

## P6 Scope

### Product Target

把 live mailbox 的“替换”逻辑限制在纯进度类邮件上，保留问题/暂停和回执类邮件。

### Mail Retention Classes

第一轮固定分类如下：

- `progress`
  - `[ACCEPTED]`
  - `[RUNNING]`
  - `[STATUS]`
- `action_required`
  - `[QUESTION]`
  - `[PAUSED]`
- `receipt`
  - `[DONE]`
  - `[FAILED]`
  - `[KILLED]`

### Replacement Semantics

第一轮 replacement 规则固定为：

- 只有 `progress` 类邮件参与自动替换
- `action_required` 不参与自动删除
- `receipt` 不参与自动删除
- `[SYNC]` 继续维持现有“只保留最新一封 sync reply”的独立规则

对同一个 thread：

- 发送任何新的 task system mail 时，都允许顺手清理更早的 `progress` 类邮件
- 但不能顺带删除更早的 `[QUESTION]`、`[PAUSED]`、`[DONE]`、`[FAILED]`、`[KILLED]`

这样第一轮的 live mailbox 结果是：

- 纯进度邮件不会无限堆积
- milestone 回执会被保留
- 用户需要处理的问题/暂停邮件也会被保留

### P6 Code Targets

至少要改到：

- `mail_runner.mail_retention`
- `mail_runner.app` 中的 thread status prune 路径
- `scripts/prune_stale_status_mails.py`
- 相关测试

第一轮不引入更复杂的“按 thread 限额保留最近 N 封 receipt/action_required”策略。

### P6 Test Gates

至少覆盖：

- `[DONE]` 到达后，旧 `[ACCEPTED]/[RUNNING]/[STATUS]` 可清理，但 `[DONE]` 被保留
- `[FAILED]`、`[KILLED]` 被归为 receipt，不再被 stale prune 收集
- `[QUESTION]`、`[PAUSED]` 被归为 action_required，不再被 stale prune 收集
- sync reply 的独立清理逻辑不回归

## Execution Order

按下面顺序执行，避免语义交叉污染：

1. 先补 P5 helper 和新增字段
2. 再接 observe / status surfaces
3. 跑 P5 定向测试
4. 然后改 P6 retention 分类和 prune 逻辑
5. 跑 P6 定向测试
6. 最后跑全量 `.\.venv\Scripts\python.exe -m pytest`
7. P6 做完后再统一重启 live host 并做烟测

## Out Of Scope

本轮明确不做：

- 自动 kill stuck thread
- 新的 `/health`、`/stuck`、`/orphaned` 命令
- Android 侧 UI 改版
- 非邮件控制面的独立桌面 UI
- 更复杂的回执归档/多级保留策略
