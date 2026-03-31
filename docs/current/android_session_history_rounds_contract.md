# Android Session History Rounds Contract

> 文档层级：Layer 1（current app-facing read contract）
>
> 当前来源：
>
> - `GET /v1/android/session-snapshot -> session_snapshot.history_rounds`
> - `GET /v1/android/session-history -> history_rounds`
>
> 当前路径：`docs/current/android_session_history_rounds_contract.md`

## 状态

- 日期：2026-03-29
- 目的：冻结当前 `history_rounds` projector 的返回语义
- 范围：round 字段、排序规则、状态回退语义，以及 `session-snapshot` 与独立 `session-history` 的共享 truth

## 1. 一句话契约

当前 repo-side 已把 Android 历史回合读面收敛为同一个 durable round projector。

同一份 `history_rounds` truth 现在同时暴露在：

- `session_snapshot.history_rounds`
- `GET /v1/android/session-history`

它的职责是给 Android 历史复盘页返回“按回合组织的 durable snapshot”，而不是 timeline replay 或 output chunk transcript。

## 2. 顶层字段

`history_rounds` 是数组，数组项固定代表一轮回合。

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
- 返回顺序仍固定为最新一轮在前

## 3. 排序规则

当前 repo-side 会先按 chronological order 做 round projection，再在最终返回前 reverse。

稳定读法：

- `history_rounds[0]` 是最新一轮
- `round_number` 仍保留自然递增编号

Android 不应把数组位置当作正式轮次号。

## 4. `created_at`

当前回退链固定为：

1. `RunResult.finished_at`
2. `RunResult.started_at`
3. `TaskSnapshot.updated_at`
4. `TaskSnapshot.created_at`
5. `SessionState.last_progress_at`
6. `SessionState.updated_at`

它表达“这一轮形成时间锚点”，不是严格意义上的用户输入时间。

## 5. `status`

当前 `status` 是 round-level 展示状态。

当前 first-pass 映射：

- `RunResult.status = success -> done`
- `RunResult.status = awaiting_user_input -> waiting_user`
- 其余 `RunResult.status` 暂按原值返回
- 若该 round 没有 `RunResult`，但它是 `session_state.current_task_id`，则回退到 `SessionState.status`
- 若它是 `queued_task_id`，则返回 `queued`
- 否则回退为 `done`

## 6. `input`

`input` 固定包含：

- `text`
- `attachments`

当前 `input.text` 取值顺序：

1. `TaskSnapshot.turn_text`
2. `TaskSnapshot.task_text`
3. `null`

`input.attachments` 当前表达的是“这一轮新带入的输入附件”，projector 会优先按 round-local 读法返回。

当前 round-local 规则为：

1. 先按 chronological order 比较当前 round 的 `TaskSnapshot.attachments` 与上一轮的附件集合
2. 如果当前集合包含上一轮全集，则只返回本轮新增的 attachment delta
3. 如果当前集合不包含上一轮全集，则把当前 `TaskSnapshot.attachments` 视为已经是 round-local 输入并原样返回

因此它不再机械地把累计 snapshot 附件在后续每一轮重复回放。

当前稳定字段为：

- `attachment_id`
- `display_name`
- `content_type`
- `size_bytes`
- `is_image`

当前 `input.attachments[].download_ref` 不承诺提供；输入附件当前只承担 round-local 展示语义，不作为 Android 历史页的稳定下载面。

## 7. `process`

`process` 固定包含：

- `items`

每个 process item 当前至少包含：

- `item_id`
- `kind`
- `created_at`
- `updated_at`
- `status`
- `text`

当前它只投影 durable process note，不追求完整过程日志。

当前已实现来源主要有：

- `awaiting_user_input` 时的待回答问题摘要
- 当前 round 仍无 `RunResult` 时，回退 `SessionState.last_summary`

## 8. `result`

`result` 固定包含：

- `text`
- `attachments`

当前 `result.text` 取值顺序：

1. `RunResult.stdout_file` 中的 assistant-visible 输出文本
2. `RunResult.summary_file` 文本
3. `RunResult.error_message`
4. humanized `RunResult.status`
5. 若该轮仍是 `current_task_id`，回退 `SessionState.last_summary`
6. 若仍无稳定结果，则返回保守占位文案

这里的目标是保留用户实际看到的多行结果文本；它仍然是 durable round result snapshot，不等于完整 transcript replay。

`result.attachments` 来自 durable artifact truth。

当前稳定字段为：

- `attachment_id`
- `display_name`
- `content_type`
- `size_bytes`
- `is_image`
- `download_ref`

其中：

- `download_ref` 是可选字段
- 只有 repo-side 已能把该 artifact 绑定到 VPS `/v1/files` truth 时，才返回 object
- 当前 object 语义与 control-plane artifact download_ref 对齐，Android 应优先按 `vps_file` 读法消费
- 非 VPS / 非 file-surface delivery 不再因为存在 external delivery 证据就自动生成用户可消费 download_ref

## 9. 当前边界

`history_rounds` 当前不负责：

- output chunk transcript
- timeline replay
- 单 round 独立拉取
- 公共分享链接
- 实时订阅

补充说明：

- `result.attachments[].download_ref` 当前只表达 VPS 文件面上的用户可消费引用
- 它不是 public URL，也不是要求 Android 透传 raw bearer token

这些能力需要通过独立 contract 冻结，不能隐式扩写本字段。
