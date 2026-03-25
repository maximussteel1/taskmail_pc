# SDK-First Stream Smoke 验证结果

## 目的

本文记录 `sdk-first` stream smoke 当前已经实际测到什么、证据在哪，以及当前剩余 gap 如何显式记录。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py`

## 已完成验证

### 1. Codex sdk-first stream smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py --backend codex
```

结果：

- 状态：成功
- `stream.events.jsonl`：存在
- `seq`：从 `1` 到 `23` 连续递增，无缺洞
- 关键事件：存在 `assistant.delta`、`assistant.completed`、`tool.started`、`tool.completed`、`turn.completed`
- `output_chunk` 候选：已成功投影
  说明：当前 smoke 将 `stream_id` **推断**为 `thread_001:task_001`，来源是本地 run identity，而不是原始事件字段自带
- 清理检查：通过
  说明：run 结束后未残留 `codex_sidecar_process.json`

证据：

- `_tmp_sdk_stream_smoke/codex-sdk-stream-smoke-20260325_022542/stream_smoke_result.json`
- `_tmp_sdk_stream_smoke/codex-sdk-stream-smoke-20260325_022542/tasks/thread_001/runs/task_001/stream.events.jsonl`
- `_tmp_sdk_stream_smoke/codex-sdk-stream-smoke-20260325_022542/tasks/thread_001/runs/task_001/result.json`
- `_tmp_sdk_stream_smoke/codex-sdk-stream-smoke-20260325_022542/smoke_result.json`

### 2. OpenCode sdk-first stream smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py --backend opencode --run-name opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated
```

结果：

- 状态：成功
- `stream.events.jsonl`：存在
- `sdk_turn.json`：存在
- `seq`：从 `1` 到 `3` 连续递增，无缺洞
- 关键事件：存在 `turn.started`、`assistant.completed`、`turn.completed`
- `output_chunk` 候选：已成功投影
  说明：当前第一版 `OpenCode` stream evidence 仍是基于 assistant parts 的 post-turn minimal projection，不是已经证明的 true incremental stream
- 当前结论：同层 persisted stream evidence 已落地；残余 gap 已收窄为 `incremental_stream_not_proven`
- 清理检查：通过
  说明：本次临时 `opencode serve` 使用端口 `51000`，run 结束后端口已关闭

证据：

- `_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/stream_smoke_result.json`
- `_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/tasks/thread_001/runs/task_001/stream.events.jsonl`
- `_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/tasks/thread_001/runs/task_001/sdk_turn.json`
- `_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/tasks/thread_001/runs/task_001/result.json`
- `_tmp_sdk_stream_smoke/opencode-sdk-stream-smoke-20260325_same_layer_stream_escalated/smoke_result.json`

## 当前已确认结论

1. `Codex SDK` 当前已经能在本地 run 目录下落稳定的 `stream.events.jsonl`，并可投影出 `output_chunk` 候选。
2. `Codex` 当前最小可用流式合同里，`seq` 可以作为稳定排序字段。
3. `stream_id` 目前还不是原始持久化字段，只能从 run identity 推断；这部分仍属于待冻结设计。
4. `OpenCode SDK` 当前已经能在本地 run 目录下落最小同层 `stream.events.jsonl`，并可投影出 `output_chunk` 候选。
5. `OpenCode` 当前 residual gap 不再是“没有 persisted stream evidence”，而是“这份 evidence 目前还是基于 assistant parts 的 post-turn projection，尚未证明 true incremental streaming”。
6. 两条真实 smoke 都留下了明确的收尾清理证据。

## 当前未覆盖项

以下内容还没有纳入这条 stream smoke：

- replay request 游标协议
- 去重 / 缺洞修复策略
- `vps-only` canonical `output_chunk` 对象正式字段冻结
- `OpenCode` 的真正逐段 streaming 能力联调

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 最小真实 `sdk-first` 任务
- `Codex` 的 persisted stream evidence
- `OpenCode` 的当前 persisted stream evidence 与 residual incremental-stream gap 记录
