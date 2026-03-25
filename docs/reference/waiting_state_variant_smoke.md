# Waiting-State Variant Smoke 使用参考

## 目的

本文记录仓库里当前用于验证多题 waiting-state 变体的独立 fixture smoke 入口。

它验证的不是 provider 可用性，而是当前运行时协议在以下场景下是否稳定：

1. 多题 `Answers:` 结构化部分回答
2. 部分回答后继续保持 `awaiting_user_input`
3. `awaiting_user_input -> /pause -> paused`
4. `paused -> /resume` 不带答案时重新打开 `[QUESTION]`
5. `paused -> /resume + Answers:` 时合并已收答案并收口到 `done`

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\waiting_state_variant_smoke.py`
- 实际实现：`mail_runner/waiting_state_variant_smoke.py`
- 运行时主入口：`mail_runner/app.py`
- 续接编译层：`mail_runner/task_compiler.py`

## 与其他文档的关系

- 当前 `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 单题真实 backend 链路见 [sdk_question_answer_smoke.md](./sdk_question_answer_smoke.md)
- 当前 waiting-state 变体实测结果与证据见 [waiting_state_variant_smoke_validation.md](./waiting_state_variant_smoke_validation.md)

## 运行方式

```powershell
.\.venv\Scripts\python.exe .\scripts\waiting_state_variant_smoke.py
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_waiting_state_variant_smoke`

## 这条 smoke 实际做了什么

脚本会走真实 `process_once()` 流程，但使用 fixture mail client 和 fixture adapter。

完整链路是：

1. 首封 `[OC]` 新任务进入 `awaiting_user_input`
2. 回复两条 `Answers:`，只保存部分答案，不触发 backend rerun
3. 发送 `/pause`，thread/session 切到 `paused`
4. 发送 `/resume` 不带答案，退出 `paused` 并重新发 `[QUESTION]`
5. 再次 `/pause`
6. 发送 `/resume + Answers:` 补齐剩余答案，续接原 `backend_session_id` 并收口到 `done`

脚本会校验：

- 每一步 `process_once` 统计值稳定
- thread/session 状态转换符合预期
- `collected_answers` 按 canonical 顺序保存
- `/resume` 不带答案不会提前触发 rerun
- 最终 resume 会合并历史答案与新答案
- 最终 snapshot `task_text` 与 `turn_text` 都包含 canonical answer summary
- `backend_transport` 在整条链路里保持 `sdk`

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- 轻量自动化回归继续放在 `tests/`
- 这条 waiting-state 变体链路通过独立脚本留存可复跑证据
- 它不消耗真实 backend 额度，但仍保留完整 `_tmp_*` 证据目录

## 清理口径

这条 smoke 是纯 fixture harness，不会拉起额外进程、sidecar 或监听端口。

因此结果文件里仍会显式记录：

- `cleanup.required = false`
- `cleanup.cleanup_ok = true`

这样后续查看证据时，可以区分“无需清理”和“应该清理但失败”。

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `waiting_state_variant_smoke.py` 的步骤或断言变化
- 多题 `Answers:`、`/pause`、`/resume` 的运行时语义变化
- canonical answer summary 的编译规则变化
