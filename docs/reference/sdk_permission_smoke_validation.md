# SDK-First Permission Smoke 验证结果

## 目的

本文记录 `sdk-first` 权限 smoke 当前已经实际测到什么、证据在哪，以及清理检查是否通过。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py`

## 已完成验证

### 1. OpenCode sdk-first permission smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py --backend opencode
```

结果：

- 三轮状态：全部 `success`
- 三轮 `backend_transport`：全部 `sdk`
- `backend_session_id`：三轮一致，续接成功
- 权限链路：`highest -> inherit -> default`
- 权限投影：通过
  说明：`highest` 轮存在 `opencode_permission_overlay.json`，且 `edit`、`bash`、`webfetch`、`doom_loop`、`external_directory` 都投影为 `allow`；`default` 轮不再生成 overlay
- 清理检查：通过
  说明：三轮临时 `opencode serve` 端口在 run 结束后都已关闭

证据：

- `_tmp_sdk_permission_smoke/opencode-sdk-permission-smoke-20260325_021329/smoke_result.json`
- `_tmp_sdk_permission_smoke/opencode-sdk-permission-smoke-20260325_021329/compiled_inherit_snapshot.json`
- `_tmp_sdk_permission_smoke/opencode-sdk-permission-smoke-20260325_021329/compiled_reset_snapshot.json`
- `_tmp_sdk_permission_smoke/opencode-sdk-permission-smoke-20260325_021329/tasks/thread_001/runs/task_001/opencode_permission_overlay.json`
- `_tmp_sdk_permission_smoke/opencode-sdk-permission-smoke-20260325_021329/tasks/thread_001/runs/task_003/sdk_turn.json`

### 2. Codex sdk-first permission smoke

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_permission_smoke.py --backend codex
```

结果：

- 三轮状态：全部 `success`
- 三轮 `backend_transport`：全部 `sdk`
- `backend_session_id`：三轮一致，续接成功
- 权限链路：`highest -> inherit -> default`
- 权限投影：通过
  说明：`highest` 轮 `sidecar_request.json` 的 `sandbox_mode` 为 `danger-full-access`；`default` 轮恢复为 `workspace-write`；三轮 `approval_policy` 都是 `never`
- 清理检查：通过
  说明：三轮 run 结束后都未残留 `codex_sidecar_process.json`

证据：

- `_tmp_sdk_permission_smoke/codex-sdk-permission-smoke-20260325_021329/smoke_result.json`
- `_tmp_sdk_permission_smoke/codex-sdk-permission-smoke-20260325_021329/compiled_inherit_snapshot.json`
- `_tmp_sdk_permission_smoke/codex-sdk-permission-smoke-20260325_021329/compiled_reset_snapshot.json`
- `_tmp_sdk_permission_smoke/codex-sdk-permission-smoke-20260325_021329/tasks/thread_001/runs/task_001/sidecar_request.json`
- `_tmp_sdk_permission_smoke/codex-sdk-permission-smoke-20260325_021329/tasks/thread_001/runs/task_003/result.json`

## 当前已确认结论

1. 当前仓库 runtime 已能按 `sdk-first` 口径跑通 `OpenCode` 与 `Codex` 两条权限续接真实链路。
2. `Permission: highest -> omit inherit -> Permission: default` 在两个 backend 上都已被独立 smoke 证实。
3. 权限字段不只停留在 snapshot / thread_state；两个 backend 的底层执行参数投影也能被证据化复核。
4. 真实 smoke 继续维持独立脚本口径，不进入 `tests/` 主测试集。
5. 两条 smoke 都留下了明确的收尾清理证据。

## 当前未覆盖项

以下内容还没有纳入这轮 permission smoke：

- approval request / 提权交互本身
- 运行中 kill 对权限高低的影响
- waiting-state 与权限切换叠加场景
- `vps-only` canonical `command/event/result` 控制面联调

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 单 session
- `sdk-first` 权限继承与显式重置
- backend-specific 权限投影
- 收尾清理证据
