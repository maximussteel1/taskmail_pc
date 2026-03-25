# SDK-First Question-Answer Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 `sdk-first` waiting-state / question-answer 续接链路的独立 smoke 入口。

它验证的不是“SDK 包能不能单独聊天”，而是：

1. 第一轮 runtime 是否会把显式 `question capsule` 落成 `awaiting_user_input`
2. `ANSWER_QUESTION` 编译出的 follow-up snapshot 是否继续走 `backend_transport=sdk`
3. 第二轮 runtime 是否真的续接同一个 native session 并完成最小文件写入
4. 两轮 smoke 结束后，临时进程或监听端口是否都已清理

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py`
- 实际实现：`mail_runner/sdk_question_answer_smoke.py`
- 续接编译层：`mail_runner/task_compiler.py`
- runtime 调度：`mail_runner/runner.py`

## 与其他文档的关系

- SDK 总体接入基线见 [sdk_integration_reference.md](./sdk_integration_reference.md)
- `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 最小真实任务 smoke 见 [sdk_runtime_smoke.md](./sdk_runtime_smoke.md)
- 当前 question-answer 实测结果与证据见 [sdk_question_answer_smoke_validation.md](./sdk_question_answer_smoke_validation.md)

## 运行方式

OpenCode：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py --backend opencode
```

Codex：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_question_answer_smoke.py --backend codex
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_sdk_question_smoke`
- `--filename <name>`：指定第二轮要创建的测试文件名
- `--file-text <text>`：指定文件内容
- `--answer-token <token>`：固定问题答案 token，便于调试
- `--opencode-command <command>`：覆盖 OpenCode CLI 前缀
- `--codex-command <command>`：覆盖 Codex CLI 前缀

## 这条 smoke 实际做了什么

脚本会：

1. 新建临时 repo 目录与独立 `task_root`
2. 生成一个最小两轮任务：
   - 第一轮必须只输出一个 `question capsule`
   - 第二轮在收到固定 token 后创建 `question_smoke_note.txt`
3. 用 `AppConfig(opencode_transport_default="sdk", codex_transport_default="sdk")` 跑 `SerialTaskRunner`
4. 第一轮结束后加载 `thread_state.json` 和最新 snapshot
5. 用 `compile_task(ParsedMailAction(action="ANSWER_QUESTION", ...))` 生成第二轮 snapshot
6. 第二轮再跑一次 runtime，并校验：
   - 第一轮状态是 `awaiting_user_input`
   - 第二轮状态是 `success`
   - 两轮 `backend_transport` 都是 `sdk`
   - 第二轮沿用第一轮的 `backend_session_id`
   - thread 最终收口到 `done`
   - pending question 已清空
   - 目标文件存在且内容正确
7. 校验清理：
   - `OpenCode`：两轮各自临时 `opencode serve` 端口在 run 结束后都已关闭
   - `Codex`：两轮各自的 `codex_sidecar_process.json` 都未残留

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- 编译层与状态机的轻量回归仍放在 `tests/`
- 真实 `sdk-first` question-answer smoke 作为独立脚本执行
- 结果通过 `_tmp_*` 产物和 reference 文档留证据

## 当前已确认的运行时行为

结合这条 smoke，当前可以确认：

1. reply continuation、`/resume` 和 `ANSWER_QUESTION` 会继承已持久化的 `backend_transport`
2. `OpenCode` / `Codex` 的 `sdk-first` runtime 都能跑通 `awaiting_user_input -> answer -> resume -> done`
3. 清理要求可以落成可复核证据，而不是只靠口头结论

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `sdk_question_answer_smoke.py` 的入口参数、验证标准或清理口径变化
- `compile_task()` 的续接 transport 规则变化
- `OpenCode` 或 `Codex` 的 waiting-state / resume 行为发生变化
