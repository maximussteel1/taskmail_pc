# SDK-First Runtime Smoke 使用参考

## 目的

本文记录仓库里当前用于 `sdk-first` runtime 接线验证的独立 smoke 入口。

它验证的不是“底层 SDK 包能不能单独说话”，而是：

1. `SerialTaskRunner` 新建任务时是否默认走 `backend_transport=sdk`
2. `OpenCode` / `Codex` 的 runtime adapter 是否真的完成一次最小文件写入任务
3. smoke 结束后，相关临时进程是否被正确清理

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py`
- 实际实现：`mail_runner/sdk_runtime_smoke.py`
- OpenCode runtime SDK adapter：`mail_runner/adapters/opencode_sdk_adapter.py`
- Codex runtime SDK adapter：`mail_runner/adapters/codex_sdk_adapter.py`

## 与其他文档的关系

- SDK 总体接入基线见 [sdk_integration_reference.md](./sdk_integration_reference.md)
- 当前 `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 这次真实结果与证据见 [sdk_runtime_smoke_validation.md](./sdk_runtime_smoke_validation.md)
- `OpenCode` 低层直连 SDK smoke 仍单独记录在 [opencode_sdk_smoke.md](./opencode_sdk_smoke.md)

## 运行方式

OpenCode：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py --backend opencode
```

Codex：

```powershell
.\.venv\Scripts\python.exe .\scripts\sdk_runtime_smoke.py --backend codex
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_sdk_runtime_smoke`
- `--filename <name>`：指定要创建的测试文件名
- `--file-text <text>`：指定文件内容
- `--opencode-command <command>`：覆盖 OpenCode CLI 前缀
- `--codex-command <command>`：覆盖 Codex CLI 前缀

## 这条 smoke 实际做了什么

脚本会：

1. 新建临时 repo 目录与独立 `task_root`
2. 生成一个不显式写 `backend_transport` 的 snapshot seed
3. 用 `AppConfig(opencode_transport_default="sdk", codex_transport_default="sdk")` 跑 `SerialTaskRunner`
4. 让 backend 在 repo 根目录创建 `smoke_note.txt`
5. 校验：
   - `RunResult.status == success`
   - `RunResult.backend_transport == sdk`
   - `backend_session_id` 非空
   - 文件存在且内容匹配
   - stdout 含 `STATUS: OK` 和 `FILE: ...`
   - `RunResult.changed_files` 含目标文件
6. 校验清理：
   - `OpenCode`：临时 `opencode serve` 端口在 run 结束后已关闭
   - `Codex`：`codex_sidecar_process.json` 在 run 结束后不存在

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- 轻量单元测试仍留在项目测试中
- 真实 SDK smoke 作为独立脚本执行
- 结果通过 `_tmp_*` 产物和 reference 文档留证据

这样做是为了避免后续开发跑 full suite 时，每次都重复消耗真实 backend 额度。

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `sdk_runtime_smoke.py` 的入口参数、验证标准或清理口径变化
- `OpenCode` 或 `Codex` 的 runtime SDK adapter 接线方式变化
- `sdk-first` 默认 transport 策略变化
