# Android Session History Rounds Contract

> 文档层级：Layer 1（current app-facing read contract）
>
> 当前来源：`GET /v1/android/session-snapshot`
>
> 当前路径：`docs/current/android_session_history_rounds_contract.md`

## 状态

- 日期：2026-03-27
- 目的：冻结当前 repo-side 已实现的 `session_snapshot.history_rounds` 返回语义
- 范围：只覆盖 `history_rounds` 字段本身，不重写整个 `session-snapshot` contract

## 1. 一句话契约

当前 repo-side 已在：

- `GET /v1/android/session-snapshot`

的 `session_snapshot` 内，增量返回：

- `history_rounds`

它的职责不是暴露原始 timeline，也不是返回逐条 output chunk，而是给 Android 历史复盘页一个可直接消费的“按回合组织的 durable snapshot”。

当前它应读成：

- repo-side 对 `TaskSnapshot + RunResult` 的 first-pass round projection
- 页面级快照，不是增量订阅
- 历史页进入时可整体读取一次，回合展开/折叠仍由客户端本地完成

## 2. 当前挂载位置

当前 `history_rounds` 不是独立 endpoint。

它固定挂在：

- `session_snapshot.history_rounds`

也就是说，当前 Android 读取历史回合时，仍走：

- `GET /v1/android/session-snapshot`

而不是单独的 `/history` 或 `/rounds` 路径。

## 3. 顶层字段

当前 `session_snapshot.history_rounds` 是一个数组，数组项固定代表一轮回合。

每个 round 当前至少包含：

- `round_id`
- `round_number`
- `created_at`
- `status`
- `speaker_label`
- `input`
- `process`
- `result`

其中：

- `round_id` 当前格式为 `hist_round_<task_id>`
- `round_number` 从旧到新按自然轮次编号，从 `1` 开始
- 实际返回顺序仍是**最新一轮在前**

## 4. 排序规则

当前 repo-side 会先按时间做 chronological round projection，再在最终返回前 reverse。

因此当前对 Android 的稳定读法是：

- `history_rounds[0]` 是最新一轮
- `round_number` 仍保留自然递增编号

这意味着：

- 返回顺序是倒序
- 轮次编号不是倒序编号

Android 不应把数组位置当作正式轮次号。

## 5. `created_at` 语义

当前 `created_at` 采用保守回退链：

1. `RunResult.finished_at`
2. `RunResult.started_at`
3. `TaskSnapshot.updated_at`
4. `TaskSnapshot.created_at`
5. `SessionState.last_progress_at`
6. `SessionState.updated_at`

当前它的职责是：

- 给历史页提供一条稳定的“这一轮形成时间”锚点

它不是严格意义上的“用户发出输入时间”。

## 6. `status` 语义

当前 `status` 是 round-level 状态，不直接等同于 raw run status。

当前 first-pass 映射规则：

- `RunResult.status = success` -> `done`
- `RunResult.status = awaiting_user_input` -> `waiting_user`
- 其余 `RunResult.status` 暂按原值返回
- 如果当前 round 没有 `RunResult`，但它是 `session_state.current_task_id`，则回退到 `SessionState.status`
- 如果它是 `queued_task_id`，则返回 `queued`
- 否则回退为 `done`

因此 Android 应把它读成“历史回合展示状态”，而不是协议底层状态机的原始 truth。

## 7. `speaker_label` 语义

当前 `speaker_label` 表示这一轮结果的输出人标签。

当前 first-pass 规则：

- `opencode` -> `OpenCode`
- 其他非空 backend -> 首字母大写形式，例如 `Codex`
- 空值 -> `TaskMail`

当前它是展示标签，不是 routing key。

## 8. `input` 字段

当前 `input` 固定包含：

- `text`
- `attachments`

### 8.1 `input.text`

当前取值顺序：

1. `TaskSnapshot.turn_text`
2. `TaskSnapshot.task_text`
3. `null`

因此：

- continuation / resume / question answer 这类有 turn text 的回合，优先显示 turn text
- 首轮或普通 new task，通常回退到 task text

### 8.2 `input.attachments`

当前 `input.attachments` 来自 `TaskSnapshot.attachments`。

每个 attachment 当前至少包含：

- `attachment_id`
- `display_name`
- `content_type`
- `size_bytes`
- `is_image`

当前 repo-side 只保证这些字段可用于历史复盘展示；
它不承诺这些 attachment 在 Android 历史页里一定具备直接下载/打开能力。

## 9. `process` 字段

当前 `process` 固定包含：

- `items`

每个 process item 当前至少包含：

- `item_id`
- `created_at`
- `status`
- `text`

当前 first-pass 只投影很轻的 durable process 线索，不追求完整过程日志。

当前已实现的来源主要有两类：

- `awaiting_user_input` 时的待回答问题摘要
- 当前 round 仍无 `RunResult` 时，回退 `SessionState.last_summary`

因此它应读成：

- “这一轮过程中仍值得回看的 durable process note”

而不是：

- 全量 streaming transcript
- output chunk replay

## 10. `result` 字段

当前 `result` 固定包含：

- `text`
- `attachments`

### 10.1 `result.text`

当前取值顺序：

1. `RunResult.summary_file` 的文本内容
2. `RunResult.error_message`
3. humanized `RunResult.status`
4. 若该轮仍是 `current_task_id`，回退 `SessionState.last_summary`
5. 若仍无稳定结果，返回保守占位文案

因此 `result.text` 当前是“稳定结果摘要优先”的字段，而不是原始 stdout/stderr。

### 10.2 `result.attachments`

当前 `result.attachments` 来自：

- `resolve_run_artifacts(...)`

即 repo-side durable artifact truth。

每个 attachment 当前字段与 `input.attachments` 相同：

- `attachment_id`
- `display_name`
- `content_type`
- `size_bytes`
- `is_image`

当前它服务于历史页的结果附件展示，不直接等同于 Android timeline attachment truth。

## 11. 当前非目标

当前 `history_rounds` 不负责：

- 逐条 output chunk
- 完整 event log
- 单 round 独立拉取
- round 级 artifact download token
- 历史页实时订阅

这些能力如果后续需要，应通过新的 contract 单独冻结，而不是隐式扩写本字段。

## 12. Android 当前推荐读法

Android 当前应按下面方式消费：

- 历史页优先读 `session_snapshot.history_rounds`
- 同页支持多回合同时展开
- 展开态直接显示 `输入 / 过程记录 / 结果 / 附件`
- `过程记录` 在回合内部继续折叠
- 如果 repo-side `history_rounds` 不可用，客户端仍可保守回退到本地 projector

这个 fallback 是当前 Android first-pass 的兼容实现，不意味着 repo-side `history_rounds` 是可随意缺失的可选能力。
