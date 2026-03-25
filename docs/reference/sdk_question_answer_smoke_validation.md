# SDK-First Question-Answer Smoke 验证结果

## 目的

本文记录 `sdk-first` waiting-state / question-answer smoke 当前已经实际测到什么、证据在哪，以及清理检查是否通过。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py`

## 已完成验证

### 1. OpenCode sdk-first question-answer smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py --backend opencode
```

结果：

- 第一轮状态：`awaiting_user_input`
- 第二轮状态：`success`
- 两轮 `backend_transport`：`sdk`
- `backend_session_id`：两轮一致，续接成功
- thread 最终状态：`done`
- 目标文件创建：成功
- 清理检查：通过
  说明：两轮临时 `opencode serve` 分别使用端口 `56608`、`56619`，run 结束后端口都已关闭

已确认差异：

- 第二轮 `OpenCode` 虽然完成了 session 续接和文件写入，但最终 assistant reply 只返回 structured run-result capsule，没有保留请求里的两行人类可读文本
- 这条差异当前已作为 observation 落在结果文件里，不阻塞“问答续接链路可用”的结论

证据：

- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/smoke_result.json`
- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/compiled_answer_snapshot.json`
- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_001/result.json`
- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_002/result.json`
- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_002/sdk_turn.json`
- `_tmp_sdk_question_smoke/opencode-sdk-question-smoke-20260325_015458/repo/question_smoke_note.txt`

### 2. Codex sdk-first question-answer smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py --backend codex
```

结果：

- 第一轮状态：`awaiting_user_input`
- 第二轮状态：`success`
- 两轮 `backend_transport`：`sdk`
- `backend_session_id`：两轮一致，续接成功
- thread 最终状态：`done`
- 目标文件创建：成功
- 清理检查：通过
  说明：两轮 run 结束后都未残留 `codex_sidecar_process.json`

已确认差异：

- 第二轮 `Codex` 保留了请求中的两行人类可读文本
- 目标文件末尾会带一个结尾换行；当前 smoke 已按“单行文本，允许末尾换行”的口径验收

证据：

- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/smoke_result.json`
- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/compiled_answer_snapshot.json`
- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_001/result.json`
- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_002/result.json`
- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/tasks/thread_001/runs/task_002/stdout.log`
- `_tmp_sdk_question_smoke/codex-sdk-question-smoke-20260325_015458/repo/question_smoke_note.txt`

## 本次中途发现并修复的问题

在首次 question-answer smoke 中发现两类问题：

1. `task_compiler.py` 只会为 `Codex` continuation 保留 `sdk` transport，导致 `OpenCode` reply/resume 理论上可能掉回 `cli`
2. smoke 自身把“人类可读 stdout 文案”和“文件末尾换行”当成强失败条件，超出了当前支持测试需要的 canonical 行为

当前已修复为：

- `compile_task()` 现在会继承当前 thread/session 已持久化的 `backend_transport`
- 若 follow-up 显式切 backend，则走目标 backend 默认 transport resolver
- smoke 验收改为以 `awaiting_user_input -> answer -> resume -> done`、session id 续接、文件写入和清理证据为主
- backend-specific reply 差异改为 observation，不再误判为链路失败

## 当前已确认结论

1. 当前仓库 runtime 已能按 `sdk-first` 口径跑通 `OpenCode` 与 `Codex` 两条 question-answer 真实链路。
2. `reply continuation`、`/resume` 和 `ANSWER_QUESTION` 当前会继承已持久化的 `backend_transport`。
3. 两个 backend 的 `backend_session_id` 在问答续接链路中都能保持稳定。
4. 真实 smoke 继续维持独立脚本口径，不进入 `tests/` 主测试集。
5. 两条 smoke 都留下了明确的收尾清理证据。

## 当前未覆盖项

以下内容还没有纳入这轮 question-answer smoke：

- 多题 `Answers:` 结构化回答
- paused 后 `/resume` 带答案与不带答案
- 提权请求 / approval 交互
- streaming / `output_chunk`
- artifact manifest
- VPS 侧 canonical `command/event/result` 控制面联调

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 单 session
- 单题 waiting-state
- `sdk-first` answer/resume 续接
- 收尾清理证据
