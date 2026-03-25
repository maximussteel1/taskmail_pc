# Artifact Contract Smoke 验证结果

## 目的

本文记录 artifact contract smoke 当前已经实际测到什么、证据在哪，以及当前已知 gap 如何记录。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py
```

结果：

- 状态：成功
- smoke 类型：fixture harness，不调用真实 backend
- `artifact_index.json`：已生成
- `artifact_file_binding_index.json`：已生成
- 解析出的 artifacts：2 个
  - `artifact-preview` / `image/png`
  - `artifact-report` / `text/markdown`
- skipped：1 条
  说明：manifest 中缺失文件被保留为 machine-readable skipped message
- 候选 `artifact_manifest`：已投影
  说明：`size` 可直接由本地文件字节数推导；`download_ref` 只有在存在 file-surface binding 时才可得
- 清理检查：通过
  说明：这条 smoke 不拉起外部进程或监听端口，结果文件显式记录 `cleanup.required=false`

## 当前已确认结论

1. 当前仓库的本地 artifact truth 仍然是 `RunArtifact + artifact_index.json`。
2. transport-facing 绑定仍与 artifact truth 分层，继续单独写在 `artifact_file_binding_index.json`。
3. 当前最小可复用的候选 `artifact_manifest` 投影里，`artifact_id / kind / name / content_type / size` 都可以从现有 truth layer 稳定推出。
4. `download_ref` 不是当前本地 artifact truth 的内建字段；只有在存在 file-surface uploaded binding 时，才有稳定来源。
5. “未绑定 artifact 没有 download_ref” 目前应被视为已知 gap，而不是异常。

## 证据

- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_023007/smoke_result.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_023007/tasks/thread_001/runs/task_001/artifacts/artifact_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_023007/tasks/thread_001/runs/task_001/artifacts/artifact_file_binding_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_023007/tasks/thread_001/runs/task_001/artifacts/manifest.json`

## 当前未覆盖项

以下内容还没有纳入这条 artifact smoke：

- 真实 relay `/v1/files` 上传
- COS external delivery
- `vps-only` canonical `artifact_manifest` 正式字段冻结
- 多 artifact 多 binding 的更复杂 supersede 策略

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 当前本地 artifact truth layer
- 单条 file-surface uploaded binding
- 候选 `artifact_manifest` 的最小投影与 gap 记录
