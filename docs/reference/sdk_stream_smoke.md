# SDK-First Stream Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 `sdk-first` 流式证据与 `output_chunk` 候选投影的独立 smoke 入口。

它验证的不是 UI 消费层，而是当前 runtime 是否已经留下足够稳定的底层证据，支撑后续 `vps-only` 的 `output_chunk` 设计。

当前口径分成两条：

1. `Codex SDK`：验证 `stream.events.jsonl` 是否存在，`seq` 是否连续，是否能投影出最小 `output_chunk` 候选
2. `OpenCode SDK`：验证当前是否已经落盘同层 incremental stream 证据；若 `sdk_turn.json.stream_mode` 不是 `event_stream_message_parts_incremental`，则继续显式记录 residual gap

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py`
- 实际实现：`mail_runner/sdk_stream_smoke.py`
- 底层执行复用：`mail_runner/sdk_runtime_smoke.py`
- 流事件加载：`mail_runner/stream_events.py`

## 与其他文档的关系

- 当前 `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 最小真实任务 smoke 见 [sdk_runtime_smoke.md](./sdk_runtime_smoke.md)
- 当前 stream 实测结果与证据见 [sdk_stream_smoke_validation.md](./sdk_stream_smoke_validation.md)

## 运行方式

Codex：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py --backend codex
```

OpenCode：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_stream_smoke.py --backend opencode
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_sdk_stream_smoke`
- `--filename <name>`：指定测试文件名
- `--file-text <text>`：指定文件内容

## 这条 smoke 实际做了什么

脚本先复用 `sdk_runtime_smoke` 跑一轮真实最小任务，因此：

- 会真实调用 `Codex SDK` / `OpenCode SDK`
- 会保留对应的清理证据
- 不挂在 `tests/` 主测试集里

在最小任务成功后，再额外做 stream 侧校验：

### Codex

1. 读取 `runs/<task_id>/stream.events.jsonl`
2. 校验 `seq` 是否从 `1` 开始连续递增
3. 校验至少存在 `assistant.delta` 和终态 `turn.completed`
4. 把带 `text` 或 `delta` 的事件投影成候选 `output_chunk`

注意：

- 当前原始持久化事件没有显式 `stream_id`
- smoke 里会把 `stream_id` **推断**为 `<thread_id>:<task_id>`
- 这只是当前本地 support-test 的候选投影，不等于 `vps-only` 协议已经最终冻结

### OpenCode

1. 读取 `runs/<task_id>/stream.events.jsonl`
2. 校验 `seq` 是否从 `1` 开始连续递增
3. 校验至少存在 `assistant.delta`、`assistant.completed` 和终态 `turn.completed`
4. 读取 `runs/<task_id>/sdk_turn.json`，校验 `stream_mode`
5. 当 `stream_mode == event_stream_message_parts_incremental` 时，把这条 run 记为 `supports_incremental_stream=true`
6. 把带 `text` 或 `delta` 的事件投影成候选 `output_chunk`
7. 只有在 `stream.events.jsonl` 缺失，或 `stream_mode` 不是 incremental 时，才把 residual gap 显式写回 smoke 结果

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- contract / parser / loader 的轻量测试继续放在 `tests/`
- 真实 `sdk-first` stream smoke 用独立脚本落证据
- 每次真实 smoke 都必须保留清理结果

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `sdk_stream_smoke.py` 的入口参数、验证标准、`stream_mode` 读法或 gap 读法变化
- `Codex SDK` 的 `stream.events.jsonl` 合同变化
- `OpenCode SDK` 的 persisted stream / incremental stream 合同或 residual gap 读法变化
