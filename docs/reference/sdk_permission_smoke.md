# SDK-First Permission Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 `sdk-first` 权限继承与重置链路的独立 smoke 入口。

它验证的不是“字段里有没有写 `Permission:`”，而是：

1. 首轮显式权限是否真实投影到 `OpenCode SDK` / `Codex SDK`
2. follow-up 省略权限时，runtime 是否继承当前 thread/session 已持久化的权限
3. follow-up 显式重置权限时，runtime 是否重新投影新的权限值
4. 每轮 smoke 结束后，临时进程或监听端口是否都已清理

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py`
- 实际实现：`mail_runner/sdk_permission_smoke.py`
- 续接编译层：`mail_runner/task_compiler.py`
- runtime 调度：`mail_runner/runner.py`

## 与其他文档的关系

- SDK 总体接入基线见 [sdk_integration_reference.md](./sdk_integration_reference.md)
- `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 最小真实任务 smoke 见 [sdk_runtime_smoke.md](./sdk_runtime_smoke.md)
- 单题 waiting-state smoke 见 [sdk_question_answer_smoke.md](./sdk_question_answer_smoke.md)
- 当前 permission 实测结果与证据见 [sdk_permission_smoke_validation.md](./sdk_permission_smoke_validation.md)

## 运行方式

OpenCode：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py --backend opencode
```

Codex：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py --backend codex
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_sdk_permission_smoke`
- `--initial-permission default|highest`：第一轮显式权限，默认 `highest`
- `--reset-permission default|highest`：第三轮显式重置权限，默认 `default`
- `--opencode-command <command>`：覆盖 OpenCode CLI 前缀
- `--codex-command <command>`：覆盖 Codex CLI 前缀

## 这条 smoke 实际做了什么

脚本会：

1. 新建临时 repo 目录与独立 `task_root`
2. 生成一个三轮链路：
   - 第一轮：显式 `Permission: highest`
   - 第二轮：省略权限，验证继承
   - 第三轮：显式 `Permission: default`，验证重置
3. 三轮都要求 backend 只回固定一行 `PERMISSION_OK | <step> | <token>`
4. 每轮都校验：
   - `RunResult.status == success`
   - `RunResult.backend_transport == sdk`
   - `backend_session_id` 非空，且三轮保持一致
   - thread 最终状态是 `done`
   - `thread_state.permission` 与期望值一致
5. 再做 backend-specific 权限投影校验：
   - `Codex`：检查 `sidecar_request.json` 里的 `sandbox_mode` 和 `approval_policy`
   - `OpenCode`：检查 `opencode_permission_overlay.json` 是否存在，以及 `highest` 时的 allow 映射
6. 校验清理：
   - `OpenCode`：每轮临时 `opencode serve` 端口在 run 结束后已关闭
   - `Codex`：每轮 `codex_sidecar_process.json` 在 run 结束后不存在

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- 状态机和解析层的轻量回归仍放在 `tests/`
- 真实 `sdk-first` 权限 smoke 作为独立脚本执行
- 结果通过 `_tmp_*` 产物和 reference 文档留证据
- 真实 smoke 的验证结论必须包含清理证据，而不只记录成功输出

## 当前已确认的运行时行为

结合这条 smoke，当前可以确认：

1. `sdk-first` runtime 下，`Permission: highest -> omit inherit -> Permission: default` 三步链路已对 `OpenCode` / `Codex` 实测可用
2. follow-up 省略权限时，会继承当前 thread/session 已持久化的权限值
3. 显式重置权限时，会更新 thread/session 持久化状态，并重新投影到底层 backend
4. 清理要求可以落成可复核证据，而不是只靠口头结论

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `sdk_permission_smoke.py` 的入口参数、验证标准或清理口径变化
- `compile_task()` 的权限继承 / 覆盖规则变化
- `OpenCode` 或 `Codex` 的权限投影方式变化
