# Artifact Contract Smoke 使用参考

## 目的

本文记录仓库里当前用于验证 artifact 真相层与候选 `artifact_manifest` 投影的独立 fixture smoke 入口。

它验证的不是外部传输服务可用性，而是当前仓库内部关于 artifact 的两层事实：

1. 当前本地 truth layer 仍是 `RunArtifact + artifact_index.json`
2. `artifact_id -> file_id/download_url` 这类 transport-facing 绑定仍单独放在 `artifact_file_binding_index.json`

同时，这条 smoke 还会把未来 `artifact_manifest` 需要的最小字段做成候选投影，便于后续 `vps-only` 设计复用。

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py`
- 实际实现：`mail_runner/artifact_contract_smoke.py`
- artifact resolver：`mail_runner/artifact_resolver.py`
- file-surface binding：`mail_runner/file_surface.py`

## 与其他文档的关系

- 当前 `vps-only` 支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
- 当前 artifact truth 的协议背景见 `docs/current/taskmail_direct_control_file_contract.md`
- 当前 artifact 实测结果与证据见 [artifact_contract_smoke_validation.md](./artifact_contract_smoke_validation.md)

## 运行方式

```powershell
.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py
```

常用可选参数：

- `--run-name <name>`：固定结果目录名，便于留证据
- `--output-dir <path>`：指定输出根目录，默认 `_tmp_artifact_contract_smoke`

## 这条 smoke 实际做了什么

脚本会：

1. 构造一个最小 `artifacts/manifest.json`
2. 准备两个真实文件和一个缺失文件
3. 走真实 `resolve_run_artifacts()` 解析 manifest
4. 写入真实 `artifact_index.json`
5. 再调用真实 `write_artifact_upload_success_binding()`，为其中一个 artifact 写入 `artifact_file_binding_index.json`
6. 基于当前 truth layer 额外投影一份候选 `artifact_manifest`

脚本会校验：

- `artifact_index.json` 已生成
- manifest 解析出的 `artifact_id / kind / name / content_type` 稳定
- 缺失文件会进入 `skipped`
- file-surface binding sidecar 不会污染 `artifact_index.json`
- 已绑定 artifact 能投影出 `download_ref`
- 未绑定 artifact 保持 `download_ref = null`

## 与 pytest 的关系

这条 smoke 当前**不挂在 `tests/` 主测试集里**。

当前口径是：

- resolver / renderer / file-surface 的轻量自动化测试继续放在 `tests/`
- 这条 smoke 单独保留一份“当前 truth layer + 候选 manifest 投影”的复核样本

## 清理口径

这条 smoke 是纯 fixture harness，不会拉起额外进程、sidecar 或监听端口。

结果文件会显式记录：

- `cleanup.required = false`
- `cleanup.cleanup_ok = true`

## 什么时候应更新本文

出现以下任一情况时，应更新：

- `artifact_contract_smoke.py` 的步骤或断言变化
- `artifact_index.json` 或 `artifact_file_binding_index.json` 的结构变化
- 仓库开始引入正式的 canonical `artifact_manifest` 对象
