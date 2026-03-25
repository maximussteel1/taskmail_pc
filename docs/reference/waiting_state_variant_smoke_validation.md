# Waiting-State Variant Smoke 验证结果

## 目的

本文记录多题 waiting-state 变体 smoke 当前已经实际测到什么、证据在哪，以及清理检查如何记录。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\waiting_state_variant_smoke.py`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\waiting_state_variant_smoke.py
```

结果：

- 状态：成功
- smoke 类型：fixture harness，不调用真实 backend
- 覆盖链路：`awaiting_user_input -> partial Answers -> paused -> /resume(no answer) -> paused -> /resume + Answers -> done`
- backend transport：整条链路保持 `sdk`
- backend session：最终 resume 续接了原 `native-session-001`
- 清理检查：通过
  说明：这条 smoke 不拉起外部进程或监听端口，结果文件显式记录 `cleanup.required=false`

## 当前已确认结论

1. 多题 `Answers:` 的部分回答会被保存到 `collected_answers`，但不会提前触发 rerun。
2. `awaiting_user_input` 下的 `/pause` 会把 thread/session 切到 `paused`，并保留 `paused_from_status=awaiting_user_input`。
3. `paused` 且仍有 pending question 时，`/resume` 不带答案会重新打开 `[QUESTION]`，不会触发 backend rerun。
4. `paused` 且仍有 pending question 时，`/resume + Answers:` 会把历史答案与新答案合并成 canonical summary，再走 resume。
5. 最终 snapshot `task_text` 与 adapter `turn_text` 都能看到规范化后的 answer summary。

## 证据

- `_tmp_waiting_state_variant_smoke/waiting-state-variant-smoke-20260325_022248/smoke_result.json`
- `_tmp_waiting_state_variant_smoke/waiting-state-variant-smoke-20260325_022248/tasks/thread_001/thread_state.json`
- `_tmp_waiting_state_variant_smoke/waiting-state-variant-smoke-20260325_022248/tasks/thread_001/snapshots/20260325_022218_019f.json`
- `_tmp_waiting_state_variant_smoke/waiting-state-variant-smoke-20260325_022248/tasks/thread_001/runs/20260325_022218_019f/result.json`

## 当前未覆盖项

以下内容还没有纳入这条 waiting-state 变体 smoke：

- 真实 `OpenCode` / `Codex` provider 调用
- approval / 提权交互
- invalid answer message 的更多分支
- `vps-only` canonical `command/event/result` 对象联调

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- fixture runtime
- 多题 waiting-state、partial answer、pause/resume 变体
- canonical answer summary 编译与续接
