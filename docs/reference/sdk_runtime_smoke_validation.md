# SDK-First Runtime Smoke 验证结果

## 目的

本文记录 `sdk-first runtime smoke` 当前已经实际测到什么、证据在哪，以及清理检查是否通过。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py`

## 已完成验证

### 1. OpenCode runtime sdk-first smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py --backend opencode
```

结果：

- 状态：成功
- `backend_transport`：`sdk`
- `backend_session_id`：非空
- 文件创建：成功
- `changed_files`：包含 `smoke_note.txt`
- 清理检查：通过
  说明：本次临时 `opencode serve` 使用端口 `58322`，run 结束后端口已关闭

证据：

- `_tmp_sdk_runtime_smoke/opencode-sdk-runtime-smoke-20260325_013241/smoke_result.json`
- `_tmp_sdk_runtime_smoke/opencode-sdk-runtime-smoke-20260325_013241/tasks/thread_001/runs/task_001/sdk_turn.json`
- `_tmp_sdk_runtime_smoke/opencode-sdk-runtime-smoke-20260325_013241/repo/smoke_note.txt`

### 2. Codex runtime sdk-first smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py --backend codex
```

结果：

- 状态：成功
- `backend_transport`：`sdk`
- `backend_session_id`：非空
- 文件创建：成功
- `changed_files`：包含 `smoke_note.txt`
- 清理检查：通过
  说明：run 结束后未残留 `codex_sidecar_process.json`

证据：

- `_tmp_sdk_runtime_smoke/codex-sdk-runtime-smoke-20260325_013256/smoke_result.json`
- `_tmp_sdk_runtime_smoke/codex-sdk-runtime-smoke-20260325_013256/repo/smoke_note.txt`

## 当前已确认结论

1. 当前仓库 runtime 新任务已经可以按 `sdk-first` 口径跑通 `OpenCode` 与 `Codex` 两条最小真实任务链路。
2. 这两条 smoke 都是独立脚本，不挂在 `tests/` 主测试集里。
3. `OpenCode` runtime SDK 路径会为每个 turn 临时拉起本地 `opencode serve`，并在结束后关闭对应监听端口。
4. `Codex` runtime SDK 路径在结束后会清掉本地 sidecar 进程记录文件。

## 本次中途发现并修复的问题

在首次 `OpenCode` runtime smoke 中，SDK 调用把 `tools=None` 发给了服务端，服务端返回 `400`。

当前已修复为：

- 只有在确实启用工具时才发送 `tools` 字段
- 否则该字段直接缺省

失败证据仍保留在：

- `_tmp_sdk_runtime_smoke/opencode-sdk-runtime-smoke-20260325_013208/smoke_result.json`

## 当前未覆盖项

以下内容还没有纳入这轮 runtime smoke：

- 多轮 follow-up / resume 续接
- `awaiting_user_input` waiting-state
- streaming / `output_chunk`
- artifact manifest
- 显式 cancel / kill 的真实 live smoke
- VPS 侧控制面对象联调

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 单 session
- 最小文件写入任务
- `sdk-first` 默认 transport 与收尾清理
